/* KindleHermes ES5-Safe Vanilla JS Client
 * Compatible with legacy Kindle WebKit AND modern Chromium.
 * Uses only var, function(), XMLHttpRequest, and simple DOM manipulation.
 */
(function () {
  var root = document.getElementById('hermes-root');
  if (!root) { return; }

  var currentScreen = root.getAttribute('data-screen') || 'home';
  var simTime = root.getAttribute('data-sim-time') || '';
  var mockOverride = root.getAttribute('data-mock') || '';
  var token = root.getAttribute('data-token') || '';
  var refreshSec = parseInt(root.getAttribute('data-refresh-sec') || '300', 10);
  var alertFlash = root.getAttribute('data-alert-flash') === '1';
  var offlinePill = document.getElementById('offline-pill');
  var busy = false;

  function buildUrl(screenName, bustCache) {
    var parts = ['screen=' + encodeURIComponent(screenName)];
    if (simTime) { parts.push('sim_time=' + encodeURIComponent(simTime)); }
    if (mockOverride) { parts.push('mock=' + encodeURIComponent(mockOverride)); }
    if (token) { parts.push('token=' + encodeURIComponent(token)); }
    if (bustCache) { parts.push('_t=' + String(new Date().getTime())); }
    return '/kindle?' + parts.join('&');
  }

  function showOffline(isOffline) {
    if (!offlinePill) { return; }
    if (isOffline) {
      offlinePill.className = 'offline-pill visible';
    } else {
      offlinePill.className = 'offline-pill';
    }
  }

  function triggerInvertFlash() {
    root.className = 'screen-root invert-flash';
    setTimeout(function () {
      root.className = 'screen-root';
    }, 350);
  }

  function findActionNode(el) {
    var cur = el;
    while (cur && cur !== document.body) {
      if (cur.getAttribute && cur.getAttribute('data-action')) {
        return cur;
      }
      cur = cur.parentNode;
    }
    return null;
  }

  function dispatchAction(actionStr, node) {
    if (busy || !actionStr) { return; }
    busy = true;

    if (node && node.className.indexOf('tap-active') === -1) {
      node.className = node.className + ' tap-active';
    }

    var xhr = new XMLHttpRequest();
    var endpoint = '/api/action' + (token ? ('?token=' + encodeURIComponent(token)) : '');
    xhr.open('POST', endpoint, true);
    xhr.setRequestHeader('Content-Type', 'application/json');

    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) { return; }
      busy = false;
      if (xhr.status >= 200 && xhr.status < 300) {
        showOffline(false);
        try {
          var res = JSON.parse(xhr.responseText);
          if (res.exit) {
            if (window.parent && window.parent !== window) {
              window.parent.postMessage({ type: 'hermes_exit' }, '*');
            }
            alert('KindleHermes: Returning to Kindle Library (on a real Kindle, KUAL exits cleanly to your books).');
            window.location.href = buildUrl('home');
            return;
          }
          var nextScreen = res.next_screen || currentScreen;
          if (window.parent && window.parent !== window) {
            window.parent.postMessage({ type: 'hermes_nav', screen: nextScreen }, '*');
          }
          window.location.href = buildUrl(nextScreen, true);
        } catch (e) {
          window.location.reload();
        }
      } else {
        showOffline(true);
        if (node) {
          node.className = node.className.replace(' tap-active', '');
        }
      }
    };

    var payload = {
      action: actionStr,
      current_screen: currentScreen
    };
    if (simTime) { payload.sim_time = simTime; }
    if (mockOverride === '1') { payload.mock = true; }
    if (mockOverride === '0') { payload.mock = false; }

    try {
      xhr.send(JSON.stringify(payload));
    } catch (err) {
      busy = false;
      showOffline(true);
    }
  }

  document.onclick = function (evt) {
    var e = evt || window.event;
    var target = e.target || e.srcElement;
    var actionNode = findActionNode(target);
    if (!actionNode) { return; }
    if (e.preventDefault) { e.preventDefault(); }
    dispatchAction(actionNode.getAttribute('data-action'), actionNode);
  };

  if (alertFlash) {
    triggerInvertFlash();
  }

  // Gentle e-ink refresh timer (minute-aligned when Pomodoro runs, 5 min otherwise)
  if (refreshSec > 0 && currentScreen.indexOf('lockscreen_') !== 0) {
    setTimeout(function () {
      var ping = new XMLHttpRequest();
      ping.open('GET', '/api/health', true);
      ping.onreadystatechange = function () {
        if (ping.readyState !== 4) { return; }
        if (ping.status === 200) {
          showOffline(false);
          window.location.href = buildUrl(currentScreen);
        } else {
          showOffline(true);
        }
      };
      try { ping.send(); } catch (err) { showOffline(true); }
    }, refreshSec * 1000);
  }
})();
