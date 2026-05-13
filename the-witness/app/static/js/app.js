function wobble() {
  const target = document.getElementById("wobbleTarget");

  if (!target) {
    return;
  }

  target.classList.remove("wobble");

  window.setTimeout(() => {
    target.classList.add("wobble");
  }, 20);
}
