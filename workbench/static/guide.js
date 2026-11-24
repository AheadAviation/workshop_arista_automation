/* Presenter guide behaviour: remembering which steps are done.
 *
 * One job, done small. The ticks live in localStorage so a presenter can close the tab
 * during prep and come back to it, and so a half-finished rehearsal is visible rather
 * than forgotten.
 *
 * Nothing here talks to a switch or to the API. This page is a document.
 */

(function () {
  'use strict';

  var STORAGE_KEY = 'fmol-guide-progress-v1';

  var boxes = Array.prototype.slice.call(document.querySelectorAll('.step-tick'));
  var pill = document.getElementById('progress-pill');
  var reset = document.getElementById('reset-progress');

  /* An earlier version of this file would have thrown on any page without the guide's
     elements, so it guards instead: a shared static file must not break the workbench
     page if it is ever loaded there by mistake. */
  if (boxes.length === 0) return;

  function read() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      var parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (err) {
      // Private mode, a quota error, or a corrupted value. Progress is a convenience,
      // never a requirement: an unreadable store means "nothing ticked yet".
      return [];
    }
  }

  function write(done) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(done));
    } catch (err) {
      /* Not being able to save a tick must not break the page. */
    }
  }

  function refresh() {
    var done = boxes.filter(function (box) { return box.checked; }).length;
    var total = boxes.length;
    var complete = done === total;

    boxes.forEach(function (box) {
      var step = box.closest('.step');
      if (step) step.classList.toggle('is-done', box.checked);
    });

    if (!pill) return;
    pill.textContent = done + ' of ' + total + ' done';
    pill.className = 'pill ' + (complete ? 'pill-ok' : done > 0 ? 'pill-warn' : 'pill-unknown');
  }

  boxes.forEach(function (box) {
    box.addEventListener('change', function () {
      write(boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.dataset.step; }));
      refresh();
    });
  });

  if (reset) {
    reset.addEventListener('click', function () {
      boxes.forEach(function (box) { box.checked = false; });
      write([]);
      refresh();
    });
  }

  // Restore on load. Only ids this page actually has, so a stale key from an older
  // version of the guide cannot tick a step that no longer exists.
  var known = boxes.map(function (b) { return b.dataset.step; });
  var saved = read().filter(function (id) { return known.indexOf(id) !== -1; });
  boxes.forEach(function (box) {
    if (saved.indexOf(box.dataset.step) !== -1) box.checked = true;
  });

  refresh();
})();
