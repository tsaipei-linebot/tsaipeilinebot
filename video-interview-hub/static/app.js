const els = {
  status: document.getElementById("status"),
  joinPanel: document.getElementById("join-panel"),
  callPanel: document.getElementById("call-panel"),
  displayName: document.getElementById("display-name"),
  joinBtn: document.getElementById("join-btn"),
  leaveBtn: document.getElementById("leave-btn"),
  localPlayer: document.getElementById("local-player"),
  remotePlayers: document.getElementById("remote-players"),
};

let appConfig = null;
let profile = null;
let agoraClient = null;
let livekitRoom = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error(`載入 ${src} 失敗`));
    document.head.appendChild(s);
  });
}

async function init() {
  const res = await fetch("/api/config");
  appConfig = await res.json();

  if (!appConfig.liffId) {
    els.status.textContent = "尚未設定 LIFF_ID，請先在 .env 填入後重新啟動服務。";
    return;
  }

  await liff.init({ liffId: appConfig.liffId });
  if (!liff.isLoggedIn()) {
    liff.login();
    return;
  }

  profile = await liff.getProfile();
  els.displayName.textContent = profile.displayName;
  els.status.hidden = true;
  els.joinPanel.hidden = false;
}

async function joinRoom() {
  const tokenRes = await fetch("/api/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identity: profile.userId, room: appConfig.testRoom }),
  });
  const data = await tokenRes.json();

  if (data.provider === "agora") {
    await loadScript("https://download.agora.io/sdk/release/AgoraRTC_N-4.20.0.js");
    await joinAgora(data);
  } else {
    await loadScript("https://cdn.jsdelivr.net/npm/livekit-client/dist/livekit-client.umd.min.js");
    await joinLiveKit(data);
  }

  els.joinPanel.hidden = true;
  els.callPanel.hidden = false;
}

async function joinAgora(data) {
  agoraClient = AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });
  await agoraClient.join(data.appId, data.channel, data.token, data.uid);

  const [audioTrack, videoTrack] = await Promise.all([
    AgoraRTC.createMicrophoneAudioTrack(),
    AgoraRTC.createCameraVideoTrack(),
  ]);
  videoTrack.play(els.localPlayer);
  await agoraClient.publish([audioTrack, videoTrack]);

  agoraClient.on("user-published", async (user, mediaType) => {
    await agoraClient.subscribe(user, mediaType);
    if (mediaType === "video") {
      const tile = document.createElement("div");
      tile.className = "video-tile";
      tile.id = `remote-${user.uid}`;
      els.remotePlayers.appendChild(tile);
      user.videoTrack.play(tile);
    }
    if (mediaType === "audio") {
      user.audioTrack.play();
    }
  });

  agoraClient.on("user-unpublished", (user) => {
    document.getElementById(`remote-${user.uid}`)?.remove();
  });
}

async function joinLiveKit(data) {
  const { Room, RoomEvent } = LivekitClient;
  livekitRoom = new Room();
  await livekitRoom.connect(data.wsUrl, data.token);
  await livekitRoom.localParticipant.enableCameraAndMicrophone();

  for (const pub of livekitRoom.localParticipant.videoTrackPublications.values()) {
    if (pub.track) els.localPlayer.appendChild(pub.track.attach());
  }

  livekitRoom.on(RoomEvent.TrackSubscribed, (track) => {
    const el = track.attach();
    el.classList.add("video-tile");
    els.remotePlayers.appendChild(el);
  });

  livekitRoom.on(RoomEvent.TrackUnsubscribed, (track) => {
    track.detach().forEach((el) => el.remove());
  });
}

function leaveRoom() {
  agoraClient?.leave();
  livekitRoom?.disconnect();
  agoraClient = null;
  livekitRoom = null;
  els.callPanel.hidden = true;
  els.joinPanel.hidden = false;
  els.remotePlayers.innerHTML = "";
  els.localPlayer.innerHTML = "";
}

els.joinBtn.addEventListener("click", () => {
  joinRoom().catch((err) => alert("加入房間失敗：" + err.message));
});
els.leaveBtn.addEventListener("click", leaveRoom);

init().catch((err) => {
  els.status.textContent = "初始化失敗：" + err.message;
});
