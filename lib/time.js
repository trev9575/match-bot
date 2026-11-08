// Times in the community's timezone (TIMEZONE, default America/New_York), whatever timezone
// the container itself runs in.
const TZ = (process.env.TIMEZONE || "America/New_York").trim();

const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: TZ, hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric",
    hour: "numeric", minute: "numeric", second: "numeric", weekday: "short"
});
const DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

function parts(date = new Date()) {
    const o = {};
    for (const p of fmt.formatToParts(date)) o[p.type] = p.value;
    return {
        year: +o.year, month: +o.month, day: +o.day, hour: +o.hour % 24, minute: +o.minute, second: +o.second,
        weekday: DAYS.indexOf(o.weekday.slice(0, 3).toLowerCase())
    };
}

// The instant when the clock in TZ shows y-m-d h:mi
function fromZoned(y, m, d, h = 0, mi = 0) {
    let guess = Date.UTC(y, m - 1, d, h, mi);
    for (let i = 0; i < 2; i++) {
        const p = parts(new Date(guess));
        const shown = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute);
        guess += Date.UTC(y, m - 1, d, h, mi) - shown;
    }
    return new Date(guess);
}

// "8pm", "tonight 9", "tomorrow 8:30pm", "fri 9pm", "saturday 20:00", "in 2h", "2026-11-14 20:00",
// or a Discord timestamp <t:1731628800:F>. A bare hour from 1 to 11 means pm (match nights are evenings).
function parseWhen(text, now = new Date()) {
    const t = String(text || "").trim().toLowerCase();
    let m;
    if ((m = t.match(/<t:(\d{9,11})(:\w)?>/))) return new Date(+m[1] * 1000);
    if ((m = t.match(/^in\s+(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?|h|hr|hrs|hours?)$/)))
        return new Date(now.getTime() + parseFloat(m[1]) * (m[2].startsWith("h") ? 3600000 : 60000));
    const today = parts(now);
    let y = today.year, mo = today.month, d = today.day, explicitDay = false;
    if ((m = t.match(/(\d{4})-(\d{1,2})-(\d{1,2})/))) {
        [y, mo, d] = [+m[1], +m[2], +m[3]]; explicitDay = true;
    } else if ((m = t.match(/\b(\d{1,2})\/(\d{1,2})\b/))) {
        [mo, d] = [+m[1], +m[2]]; explicitDay = true;
        if (fromZoned(y, mo, d, 23, 59) < now) y += 1;
    } else if (/\btomorrow\b/.test(t)) {
        const n = parts(new Date(fromZoned(y, mo, d, 12).getTime() + 86400000));
        [y, mo, d] = [n.year, n.month, n.day]; explicitDay = true;
    } else if (/\b(today|tonight)\b/.test(t)) {
        explicitDay = true;
    } else {
        const wd = DAYS.findIndex((x) => new RegExp(`\\b${x}[a-z]*\\b`).test(t));
        if (wd >= 0) {
            const ahead = (wd - today.weekday + 7) % 7;
            const n = parts(new Date(fromZoned(y, mo, d, 12).getTime() + ahead * 86400000));
            [y, mo, d] = [n.year, n.month, n.day]; explicitDay = true;
            if (ahead === 0) explicitDay = "weekday";
        }
    }
    const rest = t.replace(/\d{4}-\d{1,2}-\d{1,2}|\b\d{1,2}\/\d{1,2}\b/g, " ");
    m = rest.match(/\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?\b/);
    if (!m) return null;
    let h = +m[1];
    const mi = m[2] ? +m[2] : 0, ap = m[3];
    if (h > 23 || mi > 59) return null;
    if (ap && ap.startsWith("p") && h < 12) h += 12;
    else if (ap && ap.startsWith("a") && h === 12) h = 0;
    else if (!ap && h >= 1 && h <= 11) h += 12;
    let when = fromZoned(y, mo, d, h, mi);
    if (when < now && (!explicitDay || explicitDay === "weekday")) {
        const n = parts(new Date(fromZoned(y, mo, d, 12).getTime() + (explicitDay === "weekday" ? 7 : 1) * 86400000));
        when = fromZoned(n.year, n.month, n.day, h, mi);     // same wall-clock time, DST-safe
    }
    return when;
}

const unix = (date) => Math.floor(new Date(date).getTime() / 1000);

module.exports = { TZ, parts, fromZoned, parseWhen, unix };
