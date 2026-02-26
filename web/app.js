/**
 * Voice Calendar Scheduler — Browser WebRTC Client
 *
 * Connects to the signaling WebSocket at /ws, negotiates a WebRTC
 * peer connection with the server, and streams mic audio for
 * voice-driven calendar scheduling.
 *
 * Flow:
 *   1. Connect WebSocket to /ws
 *   2. Send "hello" → receive ICE servers
 *   3. Capture mic audio, create RTCPeerConnection
 *   4. Send SDP offer → receive SDP answer
 *   5. Audio flows bidirectionally over WebRTC
 */

(function () {
    "use strict";

    // ── DOM elements ──────────────────────────────────────────

    const statusEl = document.getElementById("status");
    const callBtn = document.getElementById("call-btn");
    const logEl = document.getElementById("log");

    // ── State ─────────────────────────────────────────────────

    let ws = null;
    let pc = null;
    let localStream = null;
    let iceServers = [];
    let inCall = false;

    // ── Logging ───────────────────────────────────────────────

    function logMsg(text, level) {
        level = level || "info";
        const entry = document.createElement("div");
        entry.className = "entry " + level;
        const ts = new Date().toLocaleTimeString();
        entry.textContent = ts + "  " + text;
        logEl.appendChild(entry);
        logEl.scrollTop = logEl.scrollHeight;
        console.log("[" + level + "]", text);
    }

    function setStatus(text, cls) {
        statusEl.textContent = text;
        statusEl.className = cls || "";
    }

    // ── WebSocket signaling ───────────────────────────────────

    function connectSignaling() {
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const url = proto + "//" + location.host + "/ws";

        logMsg("Connecting to " + url);
        setStatus("Connecting...");

        ws = new WebSocket(url);

        ws.onopen = function () {
            logMsg("Signaling connected");
            setStatus("Connected", "connected");
            // Request ICE servers
            ws.send(JSON.stringify({ type: "hello" }));
        };

        ws.onmessage = function (event) {
            var msg;
            try {
                msg = JSON.parse(event.data);
            } catch (e) {
                logMsg("Invalid JSON from server", "error");
                return;
            }

            switch (msg.type) {
                case "hello_ack":
                    iceServers = msg.ice_servers || [];
                    logMsg("Got " + iceServers.length + " ICE server(s)");
                    callBtn.disabled = false;
                    setStatus("Ready — press Call", "connected");
                    break;

                case "webrtc_answer":
                    handleAnswer(msg.sdp);
                    break;

                case "error":
                    logMsg("Server error: " + msg.message, "error");
                    setStatus("Error: " + msg.message, "error");
                    if (inCall) {
                        cleanupCall();
                    }
                    break;

                case "pong":
                    break;

                default:
                    logMsg("Unknown message: " + msg.type);
            }
        };

        ws.onclose = function () {
            logMsg("Signaling disconnected");
            setStatus("Disconnected");
            callBtn.disabled = true;
            ws = null;
            if (inCall) {
                hangUp();
            }
        };

        ws.onerror = function () {
            logMsg("WebSocket error", "error");
            setStatus("Connection error", "error");
        };
    }

    // ── WebRTC ────────────────────────────────────────────────

    async function startCall() {
        if (inCall) return;

        logMsg("Starting call...");
        setStatus("Requesting microphone...", "calling");

        try {
            // Get microphone access
            localStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                    sampleRate: 48000,
                },
                video: false,
            });
            logMsg("Microphone access granted");
        } catch (err) {
            logMsg("Mic access denied: " + err.message, "error");
            setStatus("Microphone access denied", "error");
            return;
        }

        // Build ICE server config
        var rtcConfig = { iceServers: [] };
        for (var i = 0; i < iceServers.length; i++) {
            var s = iceServers[i];
            var entry = { urls: s.urls || s.url || "" };
            if (s.username) entry.username = s.username;
            if (s.credential) entry.credential = s.credential;
            rtcConfig.iceServers.push(entry);
        }

        logMsg("Creating peer connection (" + rtcConfig.iceServers.length + " ICE servers)");
        pc = new RTCPeerConnection(rtcConfig);

        // Add mic track
        var tracks = localStream.getTracks();
        for (var t = 0; t < tracks.length; t++) {
            pc.addTrack(tracks[t], localStream);
        }

        // Handle incoming audio track from server (TTS)
        pc.ontrack = function (event) {
            logMsg("Remote audio track received");
            var audio = document.createElement("audio");
            audio.autoplay = true;
            audio.playsInline = true;
            audio.srcObject = event.streams[0] || new MediaStream([event.track]);
            document.body.appendChild(audio);
        };

        // ICE connection state
        pc.oniceconnectionstatechange = function () {
            logMsg("ICE state: " + pc.iceConnectionState);
            if (pc.iceConnectionState === "connected") {
                setStatus("In call", "connected");
            } else if (
                pc.iceConnectionState === "disconnected" ||
                pc.iceConnectionState === "failed"
            ) {
                setStatus("Call ended", "error");
                hangUp();
            }
        };

        // Create and send SDP offer (wait for ICE gathering so
        // TURN relay candidates are included — required for mobile/NAT)
        try {
            var offer = await pc.createOffer();
            await pc.setLocalDescription(offer);

            logMsg("Gathering ICE candidates...");
            setStatus("Gathering candidates...", "calling");

            // Wait for ICE gathering to complete before sending the offer.
            // Without this, the SDP has no candidates and the remote peer
            // can't reach us (especially through symmetric NATs / mobile).
            await new Promise(function (resolve) {
                if (pc.iceGatheringState === "complete") {
                    resolve();
                    return;
                }
                var timer = setTimeout(function () {
                    logMsg("ICE gathering timed out after 10s, proceeding with partial candidates", "error");
                    resolve();
                }, 10000);
                pc.onicegatheringstatechange = function () {
                    if (pc.iceGatheringState === "complete") {
                        clearTimeout(timer);
                        resolve();
                    }
                };
            });

            logMsg("Sending SDP offer (" + (pc.localDescription.sdp.match(/a=candidate/g) || []).length + " candidates)");
            setStatus("Connecting call...", "calling");

            ws.send(
                JSON.stringify({
                    type: "webrtc_offer",
                    sdp: pc.localDescription.sdp,
                })
            );

            inCall = true;
            callBtn.textContent = "Hang Up";
            callBtn.classList.add("hangup");
        } catch (err) {
            logMsg("Offer creation failed: " + err.message, "error");
            setStatus("Call failed", "error");
            cleanupCall();
        }
    }

    async function handleAnswer(sdp) {
        if (!pc) return;
        logMsg("Received SDP answer");
        try {
            await pc.setRemoteDescription(
                new RTCSessionDescription({ type: "answer", sdp: sdp })
            );
            logMsg("Remote description set");
        } catch (err) {
            logMsg("Failed to set answer: " + err.message, "error");
        }
    }

    function hangUp() {
        logMsg("Hanging up");
        // Tell the server to stop the voice loop before closing the PC
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "hangup" }));
        }
        cleanupCall();
        setStatus("Ready — press Call", "connected");
    }

    function cleanupCall() {
        inCall = false;
        callBtn.textContent = "Call";
        callBtn.classList.remove("hangup");

        if (pc) {
            pc.close();
            pc = null;
        }

        if (localStream) {
            var tracks = localStream.getTracks();
            for (var i = 0; i < tracks.length; i++) {
                tracks[i].stop();
            }
            localStream = null;
        }
    }

    // ── Button handler ────────────────────────────────────────

    callBtn.addEventListener("click", function () {
        if (inCall) {
            hangUp();
        } else {
            startCall();
        }
    });

    // ── Keepalive ping ────────────────────────────────────────

    setInterval(function () {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "ping" }));
        }
    }, 30000);

    // ── Browser close: best-effort hangup ────────────────────

    window.addEventListener("beforeunload", function () {
        if (inCall && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "hangup" }));
        }
    });

    // ── Start ─────────────────────────────────────────────────

    connectSignaling();
})();
