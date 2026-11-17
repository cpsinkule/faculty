// Instructor print output only. The server supplies the verified chart roster
// and current linked DR schedules; none of this enters student chart emails.
function dateLabel(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
  return match ? `${match[2]}/${match[3]}/${match[1]}` : value || "—";
}

function timeLabel(value) {
  const match = /^(\d{2}):(\d{2})$/.exec(value || "");
  if (!match) return value || "—";
  const hour = Number(match[1]);
  return `${hour % 12 || 12}:${match[2]} ${hour < 12 ? "AM" : "PM"}`;
}

export function roomRosterMarkup(roster, escape) {
  const identity = (student, section = student.section_number) => `<td>${escape(student.name)}</td><td class="seating-roster-uin">${escape(student.uin)}</td><td>${escape(String(section))}</td>`;
  const response = (student) => {
    const status = student.response_card || {label: "No card issued", no_response: false};
    return `<td class="seating-roster-response">${status.no_response ? `<strong>${escape(status.label)}</strong>` : escape(status.label)}</td>`;
  };
  const headings = (last) => `<thead><tr><th scope="col">Student</th><th scope="col">UIN</th><th scope="col">Section</th><th scope="col">${last}</th><th scope="col">Response card</th></tr></thead>`;
  const room = roster.room_students.map((student) => `<tr>${identity(student)}<td>${escape(student.seat)}</td>${response(student)}</tr>`).join("");
  const dr = roster.dr_students.flatMap((student) => student.bookings.map((booking) => `<tr>${identity(student, booking.section_number)}<td class="seating-roster-booking"><strong>${escape(booking.exam_type)}</strong><br>${escape(dateLabel(booking.date))} · ${escape(timeLabel(booking.start_time))}–${escape(timeLabel(booking.end_time))}<br>${escape(String(booking.length_minutes))} minutes · ${escape(booking.campus || "Campus not recorded")}<br><span>Requested ${escape(dateLabel(booking.requested_on))}</span></td>${response(student)}</tr>`)).join("");
  return `<header><h2>Room roster · ${escape(roster.title)}</h2><p>${escape(roster.room_name)} · ${escape(roster.code)} · ${escape(roster.term_label)} · Section${roster.sections.length === 1 ? "" : "s"} ${roster.sections.map((number) => escape(String(number))).join(", ")}</p>${roster.exam_date ? `<p>${escape(dateLabel(roster.exam_date))}${roster.exam_time ? ` · ${escape(roster.exam_time)}` : ""}</p>` : ""}<p>${roster.room_students.length} in room · ${roster.dr_students.length} DR tester${roster.dr_students.length === 1 ? "" : "s"}</p></header><h3>Students in ${escape(roster.room_name)} · alphabetical order</h3>${room ? `<table class="seating-roster-table seating-roster-seated">${headings("Seat")}<tbody>${room}</tbody></table>` : '<p>No students are seated in this room.</p>'}<h3>DR testers</h3><p class="seating-roster-time-note">Bookings linked to this exam for these sections. Times are local to the DR center.</p>${roster.warnings.length ? `<ul class="seating-roster-warnings">${roster.warnings.map((warning) => `<li>${escape(warning)}</li>`).join("")}</ul>` : ""}${dr ? `<table class="seating-roster-table seating-roster-dr">${headings("DR booking")}<tbody>${dr}</tbody></table>` : '<p>No DR testers for this exam in these sections.</p>'}`;
}
