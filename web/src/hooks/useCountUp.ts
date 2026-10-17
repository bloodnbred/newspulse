import { useEffect, useRef, useState } from 'react';

/** Animates from the previous value to `target`. Jumps instantly under prefers-reduced-motion. */
export function useCountUp(target: number, duration = 900): number {
  const [value, setValue] = useState(0);
  const last = useRef(0);

  /* Read during render rather than inside the effect.
   *
   * The pack's version called `setValue(target)` synchronously inside the
   * effect when reduced motion was on, which is what oxlint's
   * `react(set-state-in-effect)` rule flags. The rule is right, but the fix is
   * not "move the setState somewhere else": it is to not need it. Returning
   * `target` from render makes the first paint correct rather than
   * correct-one-frame-later, so a reduced-motion reader sees the final number
   * immediately instead of watching it climb up from zero - and that climb is
   * itself motion, which is the thing being asked us not to do. */
  const reduced =
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  useEffect(() => {
    if (reduced) {
      last.current = target;
      return;
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
  }, [target, duration, reduced]);

  return reduced ? target : value;
}
