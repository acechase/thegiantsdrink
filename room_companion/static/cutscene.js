"use strict";

window.createCutscenePlayer = () => {
  const overlay = document.getElementById("cutscene-overlay");
  const close = document.getElementById("close-cutscene");
  let latest, roomKey, dismissed, loading, image;
  const current = () => latest?.key === dismissed ? null : latest;

  function hide() {
    loading = null;
    image?.remove();
    image = null;
    overlay.hidden = true;
  }

  close.onclick = () => {
    dismissed = latest?.key;
    hide();
  };

  return {
    get current() { return current(); },
    update(art, nextRoomKey) {
      if (roomKey !== undefined && nextRoomKey !== roomKey) {
        if (art?.key === latest?.key) dismissed = art?.key;
        hide();
      }
      roomKey = nextRoomKey;
      if (art?.key !== latest?.key) hide();
      latest = art;
      if (!current()?.image || image || loading) return;
      const next = new Image();
      loading = next;
      next.alt = art.name + ". " + art.description;
      next.onerror = () => { if (loading === next) loading = null; };
      next.onload = () => {
        if (loading !== next) return;
        loading = null;
        image = next;
        overlay.prepend(next);
        overlay.hidden = false;
      };
      next.src = art.image;
    },
  };
};
