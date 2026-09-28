import { useEffect, useRef, useState } from "react";

/**
 * The part of `text` typed out so far, and whether typing is still going.
 *
 * A reply arrives in bursts — a pause while the model looks something up,
 * then a paragraph at once — and shown as it arrives it lurches. This lets it
 * out at a steady hand instead: the further behind the typing is, the faster
 * it goes, so a long burst is caught up in a couple of seconds and the tail
 * of a reply settles to a readable pace rather than stopping dead.
 *
 * Only text that arrives while `live` is typed. A reply opened from history
 * is shown whole, as is anything for a reader who asked for reduced motion.
 * Once started, typing finishes after `live` ends — the stream closes before
 * the reader has seen the last of it.
 */
export function useTypewriter(text: string, live: boolean): [string, boolean] {
  const [shown, setShown] = useState(() => (live ? 0 : text.length));
  const started = useRef(live);
  const wasLive = useRef(live);
  if (live && !wasLive.current) started.current = true;
  wasLive.current = live;

  // Text that is not a continuation of what was shown — a stopped reply the
  // server replaced — is shown whole: retyping it from the top reads as a
  // second answer.
  // An empty text is a reply starting over (regenerated), typed from the top.
  const previous = useRef(text);
  if (!text) {
    if (shown !== 0) setShown(0);
  } else if (!text.startsWith(previous.current.slice(0, shown))) {
    started.current = false;
  }
  previous.current = text;

  const animate = started.current && !prefersReducedMotion();
  const visible = animate ? Math.min(shown, text.length) : text.length;

  useEffect(() => {
    if (!animate || visible >= text.length) return;
    let frame = 0;
    let last = performance.now();
    let carry = 0;
    let at = visible;
    const tick = (now: number) => {
      const behind = text.length - at;
      // Characters a second: proportional to the backlog, with a floor so the
      // end of a reply still moves and a ceiling so a burst is not a blink.
      const rate = Math.min(1500, Math.max(90, behind * 1.5));
      carry += ((now - last) / 1000) * rate;
      last = now;
      const step = Math.floor(carry);
      if (step > 0) {
        carry -= step;
        at = Math.min(text.length, at + step);
        setShown(at);
      }
      if (at < text.length) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // `visible` is where a run starts, not something to restart it on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [animate, text]);

  return [text.slice(0, visible), animate && visible < text.length];
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true
  );
}
