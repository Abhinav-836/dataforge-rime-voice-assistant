/**
 * Real-time Telemetry and Latency Tracker
 */

import { state } from './state.js';

class MetricsTracker {
  constructor() {
    this.secondsElapsed = 0; // Starts from 00:00:00 upon connection
    this.timerInterval = null;
  }

  startTimer() {
    if (this.timerInterval) clearInterval(this.timerInterval);
    this.timerInterval = setInterval(() => {
      this.secondsElapsed += 1;
      this.updateHeaderTimer();
    }, 1000);
    this.updateHeaderTimer();
  }

  updateHeaderTimer() {
    const el = document.getElementById('header-duration-timer');
    if (!el) return;
    const hrs = Math.floor(this.secondsElapsed / 3600);
    const mins = Math.floor((this.secondsElapsed % 3600) / 60);
    const secs = this.secondsElapsed % 60;
    const fmt = `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    el.textContent = fmt;
  }

  updateTelemetry(ttft, toolTime, ttsStart, totalTurn) {
    const elTtft = document.getElementById('val-ttft');
    const elTool = document.getElementById('val-tooltime');
    const elTts = document.getElementById('val-ttsstart');
    const elTotal = document.getElementById('val-totalturn');

    if (elTtft && ttft) elTtft.textContent = `${ttft} ms`;
    if (elTool && toolTime) elTool.textContent = `${toolTime} ms`;
    if (elTts && ttsStart) elTts.textContent = `${ttsStart} ms`;
    if (elTotal && totalTurn) elTotal.textContent = `${totalTurn} s`;
  }
}

export const metricsTracker = new MetricsTracker();
