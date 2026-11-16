/* A socket stays connected in background tabs without timer throttling. */
(function () {
  "use strict";
  var socket, retry, leaving = false;
  function connect() {
    if (leaving || (socket && socket.readyState < 2)) return;
    socket = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/runtime/browser");
    socket.onclose = function () {
      if (!leaving) retry = setTimeout(connect, 2000);
    };
  }
  window.addEventListener("pagehide", function () {
    leaving = true;
    clearTimeout(retry);
    if (socket) socket.close();
  });
  window.addEventListener("pageshow", function () { leaving = false; connect(); });
  connect();
})();
