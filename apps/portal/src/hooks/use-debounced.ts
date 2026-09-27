import { useEffect, useState } from "react";

/**
 * The value as it was once it stopped changing for `delay` milliseconds.
 *
 * For anything typed that becomes a request: without it every keystroke is a
 * round trip, and the answers to the early ones arrive only to be thrown away.
 */
export function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);

  return settled;
}
