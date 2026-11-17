// A term's grading scheme (GRADEBOOK GRADING SCHEME, requirement, 2026-10-06): the gradebook setup's letter scale, shown the way
// Canvas shows a grading scheme ("100% to 90%", "< 90% to 80%", …). Pure helpers shared by the course Home's Grades row
// (courses.js), the Grading scheme dialog (gradebook-dialogs.js) and the setup page (gradebook-setup.js). The server decides every
// letter (gradebook/compute.py); nothing here changes one.

/** The standard grading scheme, which every term starts with (gradebook/setup.py SCALE). */
export const STANDARD_SCALE = [["A", "90"], ["B", "80"], ["C", "70"], ["D", "60"], ["F", "0"]];

const text = value => value === null || value === undefined ? "" : String(value).trim();
// A minimum as the server accepts it (`parse_points`, percent): 0 to 1000, at most four decimals.
const minimum = value => /^\d+(\.\d{1,4})?$/.test(text(value)) && Number(text(value)) <= 1000 ? Number(text(value)) : null;

/** Whether a scale is the standard one (same letters, same minimums). */
export function isStandardScale(scale) {
  const list = scale || [];
  return list.length === STANDARD_SCALE.length
    && STANDARD_SCALE.every(([letter, min], index) => text(list[index]?.letter) === letter && minimum(list[index]?.min) === Number(min));
}

/** The upper end of each letter's range: the top letter from 100%, every other from below the minimum of the letter above
 * ("< 90%"; "< …" while that minimum is not a number yet). A percentage above 100 (extra credit) still gets the top letter. */
export function scaleUppers(scale) {
  return (scale || []).map((entry, index) => {
    if (index === 0) return "100%";
    const above = minimum(scale[index - 1]?.min);
    return above === null ? "< …" : `< ${above}%`;
  });
}

/** One line for the course Home: "Standard · A 90% · B 80% · C 70% · D 60% · F below 60%". The last letter is the floor
 * (gradebook/compute.py): every percentage below the letter above it gets it, whatever its own minimum says. */
export function scaleSummary(scale) {
  const list = (scale || []).map(entry => ({ letter: text(entry.letter), min: minimum(entry.min) }));
  if (!list.length) return "";
  const parts = list.slice(0, -1).map(entry => `${entry.letter} ${entry.min ?? "…"}%`);
  const last = list[list.length - 1].letter;
  parts.push(list.length > 1 ? `${last} below ${list[list.length - 2].min ?? "…"}%` : `${last} for every percentage`);
  return `${isStandardScale(scale) ? "Standard · " : ""}${parts.join(" · ")}`;
}

/** Obvious mistakes in a scale, found before a save (the server still decides): [{focus, message}], the focus naming the
 * field (`scale.letter:<index>`, `scale.min:<index>`) or "" for the whole scale. */
export function scaleProblems(scale) {
  const found = [], letters = new Set();
  if (!scale.length) found.push({ focus: "", message: "The grading scheme needs at least one letter." });
  if (scale.length > 30) found.push({ focus: "", message: "The grading scheme has at most 30 letters." });
  let previous = null;
  scale.forEach((entry, index) => {
    const letter = text(entry.letter);
    if (!letter || letter.length > 4) found.push({ focus: `scale.letter:${index}`, message: `Letter ${index + 1} needs 1 to 4 characters.` });
    else if (letters.has(letter.toLowerCase())) found.push({ focus: `scale.letter:${index}`, message: `The letter ${letter} appears twice.` });
    letters.add(letter.toLowerCase());
    const value = minimum(entry.min);
    if (value === null) found.push({ focus: `scale.min:${index}`, message: `${letter || `Letter ${index + 1}`}: the minimum is a percentage from 0 to 1000.` });
    else {
      if (previous !== null && value >= previous) found.push({ focus: `scale.min:${index}`, message: `${letter || `Letter ${index + 1}`}: minimums must go down from the top letter to the last.` });
      previous = value;
    }
  });
  return found;
}
