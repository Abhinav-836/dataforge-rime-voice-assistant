/**
 * LiveKit Client Integration
 * Binds browser WebRTC audio directly to the LiveKit Agent backend.
 *
 * NOTE: voiceVisualizer is imported lazily inside the TrackSubscribed handler
 * to avoid a circular import (livekit.js ↔ voice.js). This keeps module
 * initialization deterministic across browsers and bundlers.
 */

import { state } from './state.js';
import { notifications } from './notifications.js';

class LiveKitManager {
  constructor() {
    this.room = null;
    this.localAudioTrack = null;
    this.remoteAudioTrack = null;
    this.isConnected = false;
    this.audioElement = null;
    this.audioUnlocked = false;

    // Global user interaction listener to unlock audio playback in browser
    this.bindAudioUnlockListeners();
  }

  getOrCreateAudioElement() {
    let el = document.getElementById("livekit-remote-audio");
    if (!el) {
      el = document.createElement("audio");
      el.id = "livekit-remote-audio";
      el.autoplay = true;
      el.playsInline = true;
      el.style.display = "none";
      document.body.appendChild(el);
    }
    this.audioElement = el;
    return el;
  }

  bindAudioUnlockListeners() {
    const unlock = () => {
      this.unlockAudio();
    };
    window.addEventListener("click", unlock, { passive: true });
    window.addEventListener("touchstart", unlock, { passive: true });
    window.addEventListener("keydown", unlock, { passive: true });
  }

  async unlockAudio() {
    if (this.room && typeof this.room.startAudio === "function") {
      try {
        await this.room.startAudio();
        this.audioUnlocked = true;
      } catch (e) {
        console.debug("LiveKit startAudio waiting for user gesture:", e);
      }
    }
    if (this.audioElement && this.audioElement.paused) {
      try {
        await this.audioElement.play();
      } catch (e) {
        console.debug("audioElement.play waiting for user gesture:", e);
      }
    }
  }

  async fetchToken() {
    try {
      // Primary: Request an isolated session from the server
      const sessionResp = await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          identity: "user-" + Math.random().toString(36).slice(2, 8)
        })
      });
      if (sessionResp.ok) {
        const data = await sessionResp.json();
        state.connection.sessionId = data.session_id;
        state.connection.roomName = data.room_name;
        return data;
      }

      // Fallback: Legacy token endpoint
      const resp = await fetch("/api/token");
      if (resp.ok) {
        return await resp.json();
      }
      throw new Error(`Session endpoint returned ${sessionResp.status}`);
    } catch (err) {
      console.warn("Could not fetch token from server, checking localStorage...", err);
      const token = localStorage.getItem("LIVEKIT_TOKEN");
      const url = localStorage.getItem("LIVEKIT_URL");
      if (token && url) {
        return { token, url, room: "console-demo" };
      }
      return null;
    }
  }

  async connect() {
    state.setConnectionStatus("connecting");

    try {
      const creds = await this.fetchToken();
      if (!creds || !creds.token || !creds.url) {
        console.warn("LiveKit credentials not configured. Running in interactive demo mode.");
        state.setConnectionStatus("connected");
        this.isConnected = true;
        return true;
      }

      if (typeof LivekitClient === "undefined") {
        throw new Error("LivekitClient library not loaded");
      }

      this.room = new LivekitClient.Room({
        adaptiveStream: true,
        dynacast: true,
        // FIXED (echo bug): explicit capture defaults for every mic track
        // this Room publishes, instead of relying on unstated SDK
        // defaults. Without echoCancellation explicitly on, some
        // browser/OS/driver combinations let the TTS output picked up
        // by the mic loop straight back into Deepgram STT.
        audioCaptureDefaults: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      // Track Subscribed (Agent speaking via Rime TTS)
      this.room.on(LivekitClient.RoomEvent.TrackSubscribed, (track, publication, participant) => {
        if (track.kind === LivekitClient.Track.Kind.Audio) {
          this.remoteAudioTrack = track;
          const el = this.getOrCreateAudioElement();
          track.attach(el);

          el.play().catch((err) => {
            console.warn("Audio autoplay blocked by browser policy; click anywhere on page to play voice audio.", err);
          });

          this.unlockAudio();

          if (track.mediaStream) {
            // Lazy import to break the circular dependency with voice.js.
            import('./voice.js')
              .then(({ voiceVisualizer }) => {
                voiceVisualizer.attachRemoteAudio(track.mediaStream);
              })
              .catch((err) => {
                console.warn("Could not lazily import voice visualizer:", err);
              });
          }
        }
      });

      // Track Unsubscribed
      this.room.on(LivekitClient.RoomEvent.TrackUnsubscribed, (track) => {
        if (track.kind === LivekitClient.Track.Kind.Audio) {
          track.detach();
        }
      });

      // Audio Playback Status Changed (Autoplay check)
      this.room.on(LivekitClient.RoomEvent.AudioPlaybackStatusChanged, () => {
        if (this.room && !this.room.canPlaybackAudio) {
          console.warn("Audio playback not yet permitted by browser. Click screen to enable sound.");
        }
      });

      // Active Speakers detection
      this.room.on(LivekitClient.RoomEvent.ActiveSpeakersChanged, (speakers) => {
        const isAgentSpeaking = speakers.some(s => s.identity !== this.room.localParticipant.identity);
        if (isAgentSpeaking) {
          state.notify("agent_speaking", true);
        }
      });

      // Data Channel received
      this.room.on(LivekitClient.RoomEvent.DataReceived, (payload, participant) => {
        try {
          const str = new TextDecoder().decode(payload);
          const data = JSON.parse(str);
          state.notify("data_channel_message", data);
        } catch (e) {
          // ignore non-json
        }
      });

      // Connection state changes
      this.room.on(LivekitClient.RoomEvent.Disconnected, () => {
        this.isConnected = false;
        this.localAudioTrack = null;
        state.setConnectionStatus("disconnected");
      });

      await this.room.connect(creds.url, creds.token);
      this.isConnected = true;
      state.setConnectionStatus("connected");
      state.connection.room = this.room;

      // Ensure audio element is created
      this.getOrCreateAudioElement();

      return true;
    } catch (err) {
      console.error("LiveKit connection error:", err);
      state.setConnectionStatus("error");
      notifications.error(`Connection error: ${err.message}`);
      return false;
    }
  }

  async enableMicrophone(enable = true) {
    if (!this.room) return;
    try {
      await this.unlockAudio();
      await this.room.localParticipant.setMicrophoneEnabled(enable);

      // FIXED (echo bug): capture and expose the SAME local track LiveKit
      // just published, so voice.js's visualizer can reuse it instead of
      // opening an independent getUserMedia() stream.
      if (enable) {
        const pub = this.room.localParticipant.getTrackPublication(
          LivekitClient.Track.Source.Microphone
        );
        this.localAudioTrack = pub && pub.track ? pub.track : null;
      } else {
        this.localAudioTrack = null;
      }

      state.audio.isMicActive = enable;
      state.notify("mic_state_changed", enable);
    } catch (e) {
      console.warn("Could not toggle microphone via LiveKit:", e);
    }
  }

  /**
   * Returns the raw MediaStreamTrack LiveKit is currently publishing for
   * the mic, or null if not yet available (not connected, or still
   * negotiating). voice.js should prefer this over calling
   * getUserMedia() itself.
   */
  getLocalMicMediaStreamTrack() {
    if (this.localAudioTrack && this.localAudioTrack.mediaStreamTrack) {
      return this.localAudioTrack.mediaStreamTrack;
    }
    return null;
  }

  async sendTextMessage(text) {
    if (!this.room || !this.isConnected || !this.room.localParticipant) {
      return false;
    }
    try {
      await this.unlockAudio();
      const payload = JSON.stringify({
        type: "user_chat",
        text: text,
        timestamp: Date.now() / 1000
      });
      const encoded = new TextEncoder().encode(payload);
      await this.room.localParticipant.publishData(encoded, { reliable: true });
      return true;
    } catch (e) {
      console.warn("Could not send text via LiveKit data channel:", e);
      return false;
    }
  }

  async disconnect() {
    if (this.room) {
      await this.room.disconnect();
      this.room = null;
    }
    this.isConnected = false;
    this.localAudioTrack = null;
    state.setConnectionStatus("disconnected");
  }
}

export const livekit = new LiveKitManager();