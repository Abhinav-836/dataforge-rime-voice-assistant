/**
 * Voice Audio Visualizer and Microphone Interaction
 * 
 * FIXED (echo bug): This file no longer opens its own getUserMedia() stream.
 * Instead, it reuses the single mic track that livekit.js already captured
 * and published. Two concurrent OS-level mic captures were the root cause
 * of the audible echo/feedback — the visualizer's capture had no
 * echo-cancellation constraints, so TTS output picked up by the mic looped
 * straight back into Deepgram STT.
 */

import { state } from './state.js';
import { livekit } from './livekit.js';
import { notifications } from './notifications.js';

class VoiceVisualizer {
  constructor() {
    this.audioContext = null;
    this.analyser = null;
    this.remoteAnalyser = null;
    this.micStream = null;
    this.isListening = false;
    this.isSpeaking = false;
    this.animationId = null;
    this.conversationStarted = false;
    this.micTrack = null;
    this.isInitialized = false;
  }

  async initAudio() {
    try {
      if (!this.audioContext) {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        this.audioContext = new AudioCtx();
      }
      if (this.audioContext.state === 'suspended') {
        await this.audioContext.resume();
      }

      if (this.micStream && this.micStream.active) {
        return true;
      }

      // Clean up any previous stream
      if (this.micStream) {
        this.micStream.getTracks().forEach(track => track.stop());
        this.micStream = null;
      }

      // FIXED (echo bug): Reuse the mic track LiveKit already captured and
      // published, instead of opening a second independent getUserMedia()
      // capture. Two simultaneous OS-level mic captures compete for the same
      // physical input and cause audible echo/feedback.
      const existingTrack = livekit.getLocalMicMediaStreamTrack();

      if (existingTrack) {
        this.micTrack = existingTrack;
        this.micStream = new MediaStream([existingTrack]);
      } else {
        // Fallback: only if LiveKit hasn't captured yet. Should be rare
        // because startConversation() waits for LiveKit to be connected.
        console.warn(
          "voice.js: LiveKit mic track not available yet; using fallback capture. " +
          "This may cause echo if it runs concurrently with LiveKit's capture."
        );
        this.micStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
        this.micTrack = this.micStream.getAudioTracks()[0];
      }

      this.analyser = this.audioContext.createAnalyser();
      this.analyser.fftSize = 64;
      const source = this.audioContext.createMediaStreamSource(this.micStream);
      source.connect(this.analyser);

      if (!this.animationId) {
        this.startVisualizerLoop();
      }
      this.isInitialized = true;
      return true;
    } catch (e) {
      console.warn("Microphone access not available or denied:", e);
      this.startSimulatedLoop();
      return false;
    }
  }

  attachRemoteAudio(mediaStream) {
    try {
      if (!this.audioContext) {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        this.audioContext = new AudioCtx();
      }
      if (this.audioContext.state === 'suspended') {
        this.audioContext.resume().catch(() => {});
      }
      this.remoteAnalyser = this.audioContext.createAnalyser();
      this.remoteAnalyser.fftSize = 64;
      const remoteSource = this.audioContext.createMediaStreamSource(mediaStream);
      remoteSource.connect(this.remoteAnalyser);
      if (!this.animationId) {
        this.startVisualizerLoop();
      }
    } catch (e) {
      console.warn("Could not attach remote audio to analyser:", e);
    }
  }

  startVisualizerLoop() {
    const micData = new Uint8Array(this.analyser ? this.analyser.frequencyBinCount : 32);
    const remoteData = new Uint8Array(32);

    const tick = () => {
      if (this.analyser) {
        this.analyser.getByteFrequencyData(micData);
        this.updateLeftBars(micData);
      }

      if (this.remoteAnalyser) {
        this.remoteAnalyser.getByteFrequencyData(remoteData);
        const remoteAvg = remoteData.reduce((acc, val) => acc + val, 0) / remoteData.length;
        this.updateRightBars(remoteData);
        this.updateAudioLevel(remoteAvg);

        const orb = document.getElementById('voice-orb');
        const statusDot = document.getElementById('status-dot');

        if (remoteAvg > 15) {
          this.isSpeaking = true;
          if (orb) {
            orb.classList.remove('listening');
            orb.classList.add('speaking');
          }
          if (statusDot) {
            statusDot.className = 'status-dot speaking';
          }
          const label = document.getElementById('orb-label');
          if (label) {
            label.textContent = 'Speaking...';
            label.className = 'orb-label speaking-label';
          }
          const statusText = document.getElementById('status-text');
          if (statusText) statusText.textContent = 'Speaking...';
        } else if (this.isSpeaking) {
          this.isSpeaking = false;
          if (orb) {
            orb.classList.remove('speaking');
            if (this.isListening) {
              orb.classList.add('listening');
            }
          }
          if (this.isListening) {
            const label = document.getElementById('orb-label');
            if (label) {
              label.textContent = 'Listening...';
              label.className = 'orb-label active';
            }
            const statusText = document.getElementById('status-text');
            if (statusText) statusText.textContent = 'Listening...';
          }
        }
      } else {
        this.updateSimulatedRightBars();
      }

      this.animationId = requestAnimationFrame(tick);
    };
    tick();
  }

  startSimulatedLoop() {
    const tick = () => {
      const simulatedLeft = new Uint8Array(16);
      const simulatedRight = new Uint8Array(16);
      for (let i = 0; i < 16; i++) {
        simulatedLeft[i] = Math.floor(Math.sin(Date.now() / 200 + i) * 45 + 65);
        simulatedRight[i] = Math.floor(Math.cos(Date.now() / 220 + i) * 45 + 65);
      }
      this.updateLeftBars(simulatedLeft);
      this.updateRightBars(simulatedRight);
      this.animationId = requestAnimationFrame(tick);
    };
    tick();
  }

  updateLeftBars(freqData) {
    const leftBars = document.querySelectorAll('#stage-wave-left .waveform-bar');
    leftBars.forEach((bar, idx) => {
      const val = freqData[idx % freqData.length] || 15;
      const h = Math.max(6, Math.min(48, Math.floor((val / 255) * 48)));
      bar.style.height = `${h}px`;
    });
  }

  updateRightBars(freqData) {
    const rightBars = document.querySelectorAll('#stage-wave-right .waveform-bar');
    rightBars.forEach((bar, idx) => {
      const val = freqData[idx % freqData.length] || 15;
      const h = Math.max(6, Math.min(48, Math.floor((val / 255) * 48)));
      bar.style.height = `${h}px`;
    });
  }

  updateSimulatedRightBars() {
    const rightBars = document.querySelectorAll('#stage-wave-right .waveform-bar');
    rightBars.forEach((bar, idx) => {
      const val = Math.floor(Math.sin(Date.now() / 180 + idx) * 15 + 20);
      bar.style.height = `${val}px`;
    });
  }

  updateAudioLevel(avg) {
    const levelBar = document.getElementById('audio-output-level-bar');
    const dbLabel = document.getElementById('audio-output-db');
    if (levelBar) {
      const percent = Math.min(100, Math.floor((avg / 128) * 100));
      levelBar.style.width = `${percent}%`;
    }
    if (dbLabel) {
      const db = Math.floor(-35 + (avg / 128) * 25);
      dbLabel.textContent = `${db} dB`;
    }
  }

  async startConversation() {
    // FIXED (BUG 6): Guard against clicking the orb before LiveKit is
    // connected. Without this, enableMicrophone() silently no-ops and the
    // visualizer falls back to a second getUserMedia() capture — which is
    // exactly the echo bug we just fixed.
    if (!livekit.isConnected) {
      notifications.warning("Still connecting to voice agent — please wait a moment and try again.");
      return;
    }

    // If already started and mic is off, just re-enable
    if (this.conversationStarted && !this.isListening) {
      this.isListening = true;
      state.audio.isMicActive = true;

      // FIXED (echo bug): enable LiveKit mic FIRST so its track exists,
      // then attach the visualizer to that same track (no second capture).
      await livekit.enableMicrophone(true);
      await this.initAudio();

      const orb = document.getElementById('voice-orb');
      if (orb) {
        orb.classList.add('listening');
      }
      return;
    }

    // First time starting
    if (!this.conversationStarted) {
      this.conversationStarted = true;
      this.isListening = true;
      state.audio.isMicActive = true;

      // FIXED (echo bug): order matters. LiveKit captures the mic first
      // (with echoCancellation: true), then the visualizer reuses that
      // exact track. Never the other way around.
      await livekit.enableMicrophone(true);
      await this.initAudio();

      const orb = document.getElementById('voice-orb');
      if (orb) {
        orb.classList.add('listening');
      }
    }
  }

  async stopConversation() {
    this.isListening = false;
    state.audio.isMicActive = false;
    await livekit.enableMicrophone(false);

    // FIXED (echo bug): release the local reference to LiveKit's track
    // and tear down the analyser graph. Do NOT stop() the track itself —
    // LiveKit still owns it and will handle teardown when it disables the
    // mic. Calling stop() here would kill LiveKit's published track.
    if (this.micStream) {
      this.micStream = null;
    }
    this.micTrack = null;
    this.analyser = null;

    const orb = document.getElementById('voice-orb');
    if (orb) {
      orb.classList.remove('listening', 'speaking');
    }
    const label = document.getElementById('orb-label');
    if (label) {
      label.textContent = 'Paused';
      label.className = 'orb-label';
    }
  }

  async toggleMicrophone() {
    if (!this.conversationStarted) {
      await this.startConversation();
    } else if (this.isListening) {
      await this.stopConversation();
    } else {
      await this.startConversation();
    }
  }
}

export const voiceVisualizer = new VoiceVisualizer();