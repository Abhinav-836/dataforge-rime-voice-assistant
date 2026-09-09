/**
 * Voice Audio Visualizer and Microphone Interaction
 */

import { state } from './state.js';
import { livekit } from './livekit.js';

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

      if (this.micStream) {
        this.micStream.getTracks().forEach(track => track.stop());
      }

      this.micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.micTrack = this.micStream.getAudioTracks()[0];
      
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
    // If already started and mic is off, just re-enable
    if (this.conversationStarted && !this.isListening) {
      this.isListening = true;
      state.audio.isMicActive = true;
      await livekit.enableMicrophone(true);
      
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