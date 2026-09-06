import {useEffect, useRef, useState} from 'react';

/**
 * Client-side ring buffer of portfolio value, for the intraday sparkline.
 *
 * Sampled at 1 Hz the day would be 28,800 points - far more than a 300px chart
 * can show and enough to make every re-render allocate.  We keep the last
 * `capacity` samples and thin them by `everyNth` frames, which is the same
 * trade-off a server-side downsample would make, minus the round trip.
 */
export function useValueHistory(value: number, capacity = 120, everyNth = 5): number[] {
  const buffer = useRef<number[]>([]);
  const counter = useRef(0);
  const [snapshot, setSnapshot] = useState<number[]>([]);

  useEffect(() => {
    if (value === 0) return;
    counter.current += 1;
    if (counter.current % everyNth !== 0) return;

    const next = [...buffer.current, value];
    if (next.length > capacity) next.shift();
    buffer.current = next;
    setSnapshot(next);
  }, [value, capacity, everyNth]);

  return snapshot;
}
