import { useEffect, useRef, useState } from 'react';

/** Animates from the previous value to `target`. Jumps instantly under prefers-reduced-motion. */
export function useCountUp(target: number, duration = 900): number {
  const [value, setValue] = useState(0);
  const last = useRef(0);
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      last.current = target; setValue(target); return;
    }
    const begin = last.current;
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      const v = begin + (target - begin) * eased;
      last.current = v; setValue(v);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return value;
}
