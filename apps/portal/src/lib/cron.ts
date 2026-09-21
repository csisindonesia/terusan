/**
 * A cron expression, said in words.
 *
 * The schedule is the answer to "when does this update", and `0 7 * * *` is not
 * that answer for most people who need it. Only the shapes this registry
 * actually uses are translated — a fixed time of day, on every day, on a range
 * of days of the month, or on one weekday. Anything else is handed back as
 * written rather than guessed at, because a schedule described wrongly is worse
 * than one left in its own notation.
 */

const DAYS = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

function ordinal(day: number): string {
  const suffix =
    day % 100 >= 11 && day % 100 <= 13
      ? "th"
      : day % 10 === 1
        ? "st"
        : day % 10 === 2
          ? "nd"
          : day % 10 === 3
            ? "rd"
            : "th";
  return `${day}${suffix}`;
}

export function describeSchedule(cron: string | undefined | null): string | null {
  if (!cron) return null;

  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [minute, hour, dayOfMonth, month, dayOfWeek] = parts as [
    string,
    string,
    string,
    string,
    string,
  ];

  // Only a fixed time of day is translated. A minute-wise or hourly schedule
  // would need different words and this registry has none.
  if (!/^\d+$/.test(minute) || !/^\d+$/.test(hour) || month !== "*") return cron;
  const at = `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;

  if (dayOfMonth === "*" && dayOfWeek === "*") return `${at} every day`;

  if (dayOfMonth === "*" && /^\d$/.test(dayOfWeek)) {
    return `${at} every ${DAYS[Number(dayOfWeek)] ?? dayOfWeek}`;
  }

  if (dayOfWeek === "*" && /^\d+$/.test(dayOfMonth)) {
    return `${at} on the ${ordinal(Number(dayOfMonth))} of each month`;
  }

  if (dayOfWeek === "*" && /^\d+-\d+$/.test(dayOfMonth)) {
    const [first, last] = dayOfMonth.split("-").map(Number) as [number, number];
    // A range, because agencies publish "some time in the first fortnight" and
    // the scraper looks daily until it finds the release.
    return `${at} on days ${first}–${last} of each month`;
  }

  return cron;
}
