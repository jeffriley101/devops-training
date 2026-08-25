(function (root) {
  "use strict";

  const terms = [
    { answer: "tempo", definition: "The speed of the music." },
    { answer: "forte", definition: "Play loudly." },
    { answer: "piano", definition: "Play softly." },
    { answer: "legato", definition: "Play smoothly, with notes connected." },
    { answer: "staccato", definition: "Play notes short and separated." },
    { answer: "crescendo", definition: "Gradually getting louder." },
    { answer: "diminuendo", definition: "Gradually getting softer." },
    { answer: "fermata", definition: "Hold a note or rest beyond its usual value." },
    { answer: "cadence", definition: "A musical ending or resting point." },
    { answer: "measure", definition: "A group of beats between bar lines." },
    { answer: "rhythm", definition: "The pattern of sounds and silences in time." },
    { answer: "melody", definition: "The main tune of a piece of music." },
    { answer: "harmony", definition: "Notes that sound together to support a melody." },
    { answer: "interval", definition: "The distance between two pitches." },
    { answer: "unison", definition: "Performing the same pitch together." },
    { answer: "octave", definition: "The distance between matching note names eight scale steps apart." },
    { answer: "arpeggio", definition: "The notes of a chord played one after another." },
    { answer: "chromatic", definition: "Moving or built with half steps." },
    { answer: "diatonic", definition: "Using the notes that belong to a key or scale." },
    { answer: "articulation", definition: "How each note begins, connects, and ends." },
    { answer: "dynamics", definition: "The loud and soft levels in music." },
    { answer: "syncopation", definition: "Accents placed on weak beats or between beats." },
    { answer: "embouchure", definition: "The way a player shapes the mouth to play an instrument." },
    { answer: "accelerando", definition: "Gradually getting faster." },
    { answer: "ritardando", definition: "Gradually getting slower." },
    { answer: "fortissimo", definition: "Play very loudly." },
    { answer: "pianissimo", definition: "Play very softly." },
    { answer: "marcato", definition: "Play with a strong, marked accent." },
    { answer: "tenuto", definition: "Hold a note for its full value." },
    { answer: "vibrato", definition: "A small, regular variation in pitch." },
    { answer: "intonation", definition: "How accurately pitches are played in tune." },
  ].map(function (term) { return Object.freeze(term); });

  const dataset = Object.freeze(terms);
  root.WHEEL_OF_WOODCHUCK_TERMS = dataset;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { WHEEL_OF_WOODCHUCK_TERMS: dataset };
  }
}(typeof globalThis !== "undefined" ? globalThis : this));
