// realtime_viz.js
const STREAM_IMG_ID = 'video-stream-img';
const STATUS_MSG_ID = 'status-message';
const HAZARD_TABLE_BODY_ID = 'hazard-table-body';
const ALERT_CONTAINER_ID = 'hazard-alert-container';
const LOG_FETCH_INTERVAL_MS = 3000;
const MAX_ROWS = 150;

const streamImage = document.getElementById(STREAM_IMG_ID);
const statusMessage = document.getElementById(STATUS_MSG_ID);
const hazardTableBody = document.getElementById(HAZARD_TABLE_BODY_ID);
const alertContainer = document.getElementById(ALERT_CONTAINER_ID);

let ws = null;

function getSeverityClass(severity){
    const s = String(severity || '').toUpperCase();
    if(s === 'CRITICAL' || Number(severity) >= 9) return 'badge-critical';
    if(s === 'HIGH' || Number(severity) >= 7) return 'badge-high';
    if(s === 'MEDIUM' || Number(severity) >= 4) return 'badge-medium';
    return 'badge-low';
}

function connectWebSocket(){
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = wsProtocol + "//127.0.0.1:8000/ws/video";
    statusMessage.textContent = "Status: CONNECTING...";
    ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
        console.log("WS connected");
        statusMessage.textContent = "Status: CONNECTED — waiting for frames...";
    };

    ws.onmessage = event => {
        // text message (FRAME:base64 or hazard JSON as text)
        if (typeof event.data === "string") {
            const txt = event.data;

            // frame prefix
            if (txt.startsWith("FRAME:")) {
                const b64 = txt.substring(6);
                if (streamImage) {
                    streamImage.src = "data:image/jpeg;base64," + b64;
                    statusMessage.textContent = "Status: STREAMING";
                }
                return;
            }

            // try parse hazard JSON
            try {
                const hazard = JSON.parse(txt);
                if (hazard && hazard.type) {
                    showAlert(hazard);
                    // also refresh logs immediately
                    updateHazardLog();
                }
            } catch(e){
                // not JSON - ignore
            }
            return;
        }

        // binary data fallback
        if (event.data instanceof ArrayBuffer) {
            const blob = new Blob([event.data], {type: 'image/jpeg'});
            const url = URL.createObjectURL(blob);
            streamImage.src = url;
            setTimeout(() => URL.revokeObjectURL(url), 2000);
            statusMessage.textContent = "Status: STREAMING";
        }
    };

    ws.onclose = () => {
        console.log("WS closed. Reconnecting in 1s…");
        statusMessage.textContent = "Status: DISCONNECTED — reconnecting…";
        setTimeout(connectWebSocket, 1000);
    };

    ws.onerror = err => {
        console.error("WS error:", err);
        try { ws.close(); } catch(e){}
    };
}

function showAlert(hazard){
    if(!alertContainer) return;
    const el = document.createElement('div');
    el.className = 'alert-badge';
    el.textContent = `${hazard.type} (${hazard.severity}) @${hazard.frame_id ?? ''}`;
    // auto remove after 6s
    alertContainer.prepend(el);
    setTimeout(()=> {
        try { el.remove(); } catch(e){}
    }, 6000);
}

function updateHazardLog(){
    fetch('/api/get_hazard_logs')
        .then(r => {
            if(!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(logs => {
            if(!hazardTableBody) return;
            hazardTableBody.innerHTML = '';
            if(!Array.isArray(logs) || logs.length === 0){
                hazardTableBody.innerHTML = '<tr><td colspan="4">No logs yet.</td></tr>';
                return;
            }
            const limited = logs.slice(0, MAX_ROWS);
            limited.forEach(l => {
                const tr = document.createElement('tr');
                const tdId = document.createElement('td'); tdId.textContent = l.db_id;
                const tdType = document.createElement('td'); tdType.textContent = l.type;
                const tdLvl = document.createElement('td');
                const span = document.createElement('span'); span.className = 'badge ' + getSeverityClass(l.severity);
                span.textContent = l.severity;
                tdLvl.appendChild(span);
                const tdTime = document.createElement('td'); tdTime.textContent = l.time;
                tr.appendChild(tdId); tr.appendChild(tdType); tr.appendChild(tdLvl); tr.appendChild(tdTime);
                hazardTableBody.appendChild(tr);
            });
        })
        .catch(err => {
            console.error("Error fetching logs:", err);
            if(hazardTableBody) hazardTableBody.innerHTML = '<tr><td colspan="4">Failed to load logs.</td></tr>';
        });
}

document.addEventListener('DOMContentLoaded', () => {
    connectWebSocket();
    updateHazardLog();
    setInterval(updateHazardLog, LOG_FETCH_INTERVAL_MS);
});
