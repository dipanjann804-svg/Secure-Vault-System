/* Tap-to-talk assistant.
 *
 * Speech recognition and speech synthesis stay in the browser (en-US). Only
 * the recognized text is sent to the server. The UI never offers a text box
 * for commands, and the server requires an explicit Confirm before mutations.
 *
 * Visual states: IDLE, LISTENING, PROCESSING, SPEAKING,
 * WAITING_FOR_CONFIRMATION, ERROR. Speaking can be interrupted at any time by
 * tapping the microphone again, and the last answer can be repeated.
 */
(function () {
  'use strict';

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const panel = document.getElementById('voicePanel');
  const fab = document.getElementById('voiceFab');
  const closeBtn = document.getElementById('voiceCloseBtn');
  const micBtn = document.getElementById('voiceMicBtn');
  const micLabel = document.getElementById('voiceMicLabel');
  const statusEl = document.getElementById('voiceStatus');
  const stateEl = document.getElementById('voiceState');
  const stopBtn = document.getElementById('voiceStopBtn');
  const repeatBtn = document.getElementById('voiceRepeatBtn');
  const log = document.getElementById('voiceLog');
  const pendingBox = document.getElementById('voicePending');
  const pendingText = document.getElementById('voicePendingText');
  const confirmBtn = document.getElementById('voiceConfirmBtn');
  const cancelBtn = document.getElementById('voiceCancelBtn');

  if (!panel || !fab || !micBtn) return;

  const STATE_LABELS = {
    IDLE: 'Idle',
    LISTENING: 'Listening',
    PROCESSING: 'Processing',
    SPEAKING: 'Speaking',
    WAITING_FOR_CONFIRMATION: 'Awaiting confirmation',
    ERROR: 'Error'
  };

  let recognition = null;
  let listening = false;
  let processing = false;
  let sentThisTurn = false;
  let pending = null;
  let lastSpoken = '';

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  function setState(state) {
    const key = STATE_LABELS[state] ? state : 'IDLE';
    panel.setAttribute('data-voice-state', key);
    if (stateEl) {
      stateEl.setAttribute('data-state', key);
      stateEl.textContent = STATE_LABELS[key];
    }
    micBtn.classList.toggle('listening', key === 'LISTENING');
    micBtn.classList.toggle('processing', key === 'PROCESSING');
    micBtn.classList.toggle('speaking', key === 'SPEAKING');
    if (key === 'LISTENING') {
      micLabel.textContent = 'Listening… tap to stop';
      micBtn.setAttribute('aria-label', 'Stop listening');
    } else if (key === 'PROCESSING') {
      micLabel.textContent = 'Thinking…';
      micBtn.setAttribute('aria-label', 'Processing voice command');
    } else if (key === 'SPEAKING') {
      micLabel.textContent = 'Speaking… tap to interrupt';
      micBtn.setAttribute('aria-label', 'Interrupt the assistant and speak');
    } else {
      micLabel.textContent = 'Tap to talk';
      micBtn.setAttribute('aria-label', 'Start listening');
    }
    if (stopBtn) stopBtn.disabled = key !== 'SPEAKING';
    if (repeatBtn) repeatBtn.disabled = !lastSpoken;
  }

  function idleState() {
    setState(pending ? 'WAITING_FOR_CONFIRMATION' : 'IDLE');
  }

  function appendMessage(role, text, extraClass) {
    if (!text) return;
    const bubble = document.createElement('div');
    bubble.className = 'voice-msg ' + role + (extraClass ? ' ' + extraClass : '');
    bubble.textContent = text;
    log.appendChild(bubble);
    log.scrollTop = log.scrollHeight;
  }

  function appendShareCard(url) {
    if (!url) return;
    const card = document.createElement('div');
    card.className = 'voice-link-card';
    const row = document.createElement('div');
    row.className = 'voice-link-row';
    const input = document.createElement('input');
    input.type = 'text';
    input.readOnly = true;
    input.value = url;
    input.setAttribute('aria-label', 'New share link');
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.textContent = 'Copy';
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(url);
      } catch (err) {
        input.select();
        try { document.execCommand('copy'); } catch (e) {}
      }
      copy.textContent = 'Copied';
      setTimeout(() => { copy.textContent = 'Copy'; }, 1400);
    });
    row.appendChild(input);
    row.appendChild(copy);
    card.appendChild(row);
    log.appendChild(card);
    log.scrollTop = log.scrollHeight;
  }

  function stopSpeaking() {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    if (!processing && !listening) idleState();
  }

  function speak(text) {
    if (!text) return;
    lastSpoken = String(text).slice(0, 600);
    if (repeatBtn) repeatBtn.disabled = false;
    if (!window.speechSynthesis) return;
    const utter = new SpeechSynthesisUtterance(lastSpoken);
    utter.lang = 'en-US';
    utter.rate = 1.02;
    utter.pitch = 1;
    window.speechSynthesis.cancel();
    utter.onend = () => {
      if (!processing && !listening) idleState();
    };
    utter.onerror = () => {
      if (!processing && !listening) idleState();
    };
    setState('SPEAKING');
    window.speechSynthesis.speak(utter);
  }

  function repeatLast() {
    if (!lastSpoken) return;
    appendMessage('assistant', lastSpoken);
    speak(lastSpoken);
  }

  function setPending(value) {
    pending = value || null;
    if (pending && pending.nonce) {
      pendingText.textContent =
        pending.summary +
        (pending.expires_in ? ' Confirm within ' + pending.expires_in + ' seconds.' : '');
      pendingBox.classList.add('open');
      if (!processing && !listening) setState('WAITING_FOR_CONFIRMATION');
    } else {
      pendingText.textContent = '';
      pendingBox.classList.remove('open');
      if (!processing && !listening) setState('IDLE');
    }
  }

  function setProcessing(value) {
    processing = value;
    micBtn.disabled = value;
    if (value) {
      setState('PROCESSING');
      setStatus('Checking that with your vault…');
    } else {
      micBtn.disabled = false;
      if (!listening) {
        idleState();
        setStatus('Ready. Microphone stays off until you tap.');
      }
    }
  }

  function applyServerResponse(data, httpStatus) {
    const status = data.status || 'error';
    const display = data.display || data.response_text || data.speak || 'Something went wrong.';
    if (status === 'error') {
      appendMessage('assistant', display, 'error');
      setState('ERROR');
    } else if (status === 'blocked') {
      appendMessage('assistant', display, 'blocked');
    } else {
      appendMessage('assistant', display);
    }
    if (data.speak) speak(data.speak);

    if (data.pending) setPending(data.pending);

    const action = data.client_action;
    if (action && action.type) {
      if (action.type === 'open_preview' && action.doc_id) {
        if (typeof window.openPreviewById === 'function') {
          window.openPreviewById(action.doc_id);
        } else {
          window.location.href = '/dashboard';
        }
      } else if (action.type === 'open_settings') {
        const btn = document.getElementById('openSettingsBtn');
        if (btn) btn.click();
      } else if (action.type === 'open_trash') {
        const btn = document.getElementById('openTrashBtn');
        if (btn) btn.click();
      } else if (action.type === 'navigate' && action.url) {
        setTimeout(() => { window.location.href = action.url; }, 700);
      } else if (action.type === 'share_link' && action.url) {
        appendShareCard(action.url);
      }
    }

    if (httpStatus === 429 || httpStatus >= 500) {
      setStatus('Please wait a moment and try again.');
    }
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    let data = {};
    try { data = await res.json(); } catch (e) {}
    if (!res.ok && !data.speak) {
      data.status = 'error';
      data.speak = 'The assistant could not complete that request.';
      data.display = data.message || data.speak;
    }
    return { res, data };
  }

  async function sendCommand(text) {
    appendMessage('user', text);
    setProcessing(true);
    try {
      const { res, data } = await postJson('/voice/command', {
        text: text,
        nonce: pending ? pending.nonce : null
      });
      applyServerResponse(data, res.status);
      if (/^(confirm|yes|yes please|go ahead|do it|cancel|no|never mind|nevermind|stop|forget it)$/i.test(text.trim())) {
        setPending(null);
      }
    } catch (err) {
      appendMessage('assistant', 'Network error. Please try again.', 'error');
      setStatus('Network error. Check your connection.');
      setState('ERROR');
    } finally {
      setProcessing(false);
      sentThisTurn = false;
    }
  }

  async function confirmPending() {
    if (!pending || !pending.nonce || processing) return;
    setProcessing(true);
    try {
      const { res, data } = await postJson('/voice/confirm', { nonce: pending.nonce });
      setPending(null);
      applyServerResponse(data, res.status);
    } catch (err) {
      appendMessage('assistant', 'Network error. Please try again.', 'error');
      setState('ERROR');
    } finally {
      setProcessing(false);
    }
  }

  async function cancelPending() {
    if (processing) return;
    setProcessing(true);
    try {
      const { res, data } = await postJson('/voice/cancel', {
        nonce: pending ? pending.nonce : null
      });
      setPending(null);
      applyServerResponse(data, res.status);
    } catch (err) {
      appendMessage('assistant', 'Network error. Please try again.', 'error');
      setState('ERROR');
    } finally {
      setProcessing(false);
    }
  }

  async function restoreState() {
    try {
      const res = await fetch('/voice/state', { method: 'GET' });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setPending(data.pending || null);
        if (data.ai && data.ai.state && data.ai.state !== 'ready') {
          setStatus('Assistant ready. General questions need a Gemini key; vault commands work offline.');
        }
      }
    } catch (e) {}
  }

  function stopListening() {
    if (recognition && listening) {
      try { recognition.stop(); } catch (e) {}
    }
  }

  function buildRecognition() {
    if (!SpeechRecognition) {
      micBtn.disabled = true;
      setState('ERROR');
      setStatus('Speech recognition is not available in this browser. Use Chrome or Edge.');
      return null;
    }
    const rec = new SpeechRecognition();
    rec.lang = 'en-US';
    rec.continuous = false;
    rec.interimResults = true;
    rec.maxAlternatives = 1;

    rec.onstart = () => {
      listening = true;
      sentThisTurn = false;
      setState('LISTENING');
      setStatus('Listening… speak now.');
    };

    rec.onresult = (event) => {
      let interim = '';
      let finalText = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const piece = event.results[i][0].transcript || '';
        if (event.results[i].isFinal) finalText += piece;
        else interim += piece;
      }
      if (interim) setStatus('Listening… ' + interim.trim());
      if (finalText && !sentThisTurn) {
        sentThisTurn = true;
        stopListening();
        sendCommand(finalText.trim());
      }
    };

    rec.onerror = (event) => {
      const code = event.error || '';
      setState('ERROR');
      if (code === 'no-speech') {
        setStatus('I didn’t hear anything. Tap the mic and try again.');
      } else if (code === 'not-allowed' || code === 'service-not-allowed') {
        setStatus('Microphone access is blocked. Allow it in the browser and try again.');
      } else if (code === 'audio-capture') {
        setStatus('No microphone was found. Check your device and try again.');
      } else if (code === 'network') {
        setStatus('The browser speech service is offline. Check your connection.');
      } else if (code === 'aborted') {
        setStatus('Stopped listening.');
        idleState();
      } else {
        setStatus('Speech recognition failed. Please try again.');
      }
    };

    rec.onend = () => {
      listening = false;
      if (!processing) {
        idleState();
        if (statusEl.textContent.startsWith('Listening')) {
          setStatus('Ready. Microphone stays off until you tap.');
        }
      }
    };
    return rec;
  }

  function openPanel() {
    panel.classList.add('open');
    panel.setAttribute('aria-hidden', 'false');
    fab.classList.add('open');
    fab.setAttribute('aria-expanded', 'true');
    restoreState();
    (micBtn.disabled ? closeBtn : micBtn).focus();
  }

  function closePanel() {
    stopListening();
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    panel.classList.remove('open');
    panel.setAttribute('aria-hidden', 'true');
    fab.classList.remove('open');
    fab.setAttribute('aria-expanded', 'false');
    fab.focus();
  }

  fab.addEventListener('click', () => {
    if (panel.classList.contains('open')) closePanel();
    else openPanel();
  });
  closeBtn.addEventListener('click', closePanel);
  micBtn.addEventListener('click', () => {
    if (processing) return;
    if (listening) {
      stopListening();
      return;
    }
    // Tapping the mic while the assistant is speaking interrupts it and starts
    // a new question immediately.
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    if (!recognition) recognition = buildRecognition();
    if (!recognition) return;
    try {
      recognition.start();
      setStatus('Starting microphone…');
    } catch (err) {
      setStatus('Could not start the microphone. Try again.');
      setState('ERROR');
    }
  });
  if (stopBtn) stopBtn.addEventListener('click', stopSpeaking);
  if (repeatBtn) repeatBtn.addEventListener('click', repeatLast);
  confirmBtn.addEventListener('click', confirmPending);
  cancelBtn.addEventListener('click', cancelPending);

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && panel.classList.contains('open')) closePanel();
  });

  recognition = buildRecognition();
  setState('IDLE');
})();
