// A Response Card issue's title as the instructor reads it (RESPONSE-CARDS: titles keep their stored placeholders). Shared by the
// Response Cards page and the course pages' work list (courses.js), which imports only this
// small module rather than the whole page.

// Issue-level placeholders the page can name; student variables stay literal (each card has its own).
export const LABEL_VARIABLES = {"exam.name": (issue) => issue.assignment_title, "assignment.name": (issue) => issue.assignment_title,
  "course.code": (issue) => issue.course_code, "exam.room": (issue) => issue.room_name};

/** An issue's title with the placeholders the issue itself fixes filled in ({{exam.name}}, {{course.code}}, {{exam.room}}). */
export function issueLabel(issue) {
  return String(issue?.title || "Response card").replace(/\{\{\s*([a-z_.]+)\s*\}\}/g, (match, key) =>
    Object.hasOwn(LABEL_VARIABLES, key) ? LABEL_VARIABLES[key](issue) || match : match);
}
