(function (root) {
  "use strict";

  const scales = Object.freeze([
    { key: "c-major", name: "C Major", rootMidi: 60, notes: [[0, "C"], [2, "D"], [4, "E"], [5, "F"], [7, "G"], [9, "A"], [11, "B"], [12, "C"]] },
    { key: "f-major", name: "F Major", rootMidi: 53, notes: [[0, "F"], [2, "G"], [4, "A"], [5, "B♭"], [7, "C"], [9, "D"], [11, "E"], [12, "F"]] },
    { key: "b-flat-major", name: "B♭ Major", rootMidi: 58, notes: [[0, "B♭"], [2, "C"], [4, "D"], [5, "E♭"], [7, "F"], [9, "G"], [11, "A"], [12, "B♭"]] },
    { key: "e-flat-major", name: "E♭ Major", rootMidi: 51, notes: [[0, "E♭"], [2, "F"], [4, "G"], [5, "A♭"], [7, "B♭"], [9, "C"], [11, "D"], [12, "E♭"]] },
    { key: "g-major", name: "G Major", rootMidi: 55, notes: [[0, "G"], [2, "A"], [4, "B"], [5, "C"], [7, "D"], [9, "E"], [11, "F♯"], [12, "G"]] },
    { key: "d-major", name: "D Major", rootMidi: 50, notes: [[0, "D"], [2, "E"], [4, "F♯"], [5, "G"], [7, "A"], [9, "B"], [11, "C♯"], [12, "D"]] },
    { key: "a-major", name: "A Major", rootMidi: 57, notes: [[0, "A"], [2, "B"], [4, "C♯"], [5, "D"], [7, "E"], [9, "F♯"], [11, "G♯"], [12, "A"]] },
    { key: "e-major", name: "E Major", rootMidi: 52, notes: [[0, "E"], [2, "F♯"], [4, "G♯"], [5, "A"], [7, "B"], [9, "C♯"], [11, "D♯"], [12, "E"]] },
  ]);

  root.SCALE_KEYBOARD_SCALES = scales;
  if (typeof module !== "undefined" && module.exports) module.exports = { SCALE_KEYBOARD_SCALES: scales };
}(typeof globalThis !== "undefined" ? globalThis : this));
