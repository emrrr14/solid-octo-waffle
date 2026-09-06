import {useMemo} from 'react';
import {View} from 'react-native';
import Svg, {Path, Line} from 'react-native-svg';

import {colors} from '../theme';

/**
 * Intraday shape of the portfolio, drawn from the ring buffer the stream hook
 * keeps (see `useValueHistory`).  No chart library: a polyline over ~120 points
 * is a dozen lines of path maths, and the smallest charting dependency here
 * costs more bundle and more native surface than the feature is worth.
 *
 * The dashed reference line is the previous close - a portfolio chart without
 * it shows movement but not whether the day is up or down.
 */
interface Props {
  values: number[];
  baseline: number;
  width: number;
  height: number;
}

export function Sparkline({values, baseline, width, height}: Props) {
  const {path, baselineY} = useMemo(() => {
    if (values.length < 2) return {path: '', baselineY: height / 2};

    const all = [...values, baseline];
    const min = Math.min(...all);
    const max = Math.max(...all);
    // A flat series must not divide by zero, and must not be drawn as a
    // dramatic zigzag through floating-point noise either.
    const span = max - min || Math.max(Math.abs(max) * 1e-6, 1e-6);
    const stepX = width / (values.length - 1);
    const y = (v: number) => height - ((v - min) / span) * height;

    const d = values
      .map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * stepX).toFixed(2)},${y(v).toFixed(2)}`)
      .join(' ');

    return {path: d, baselineY: y(baseline)};
  }, [values, baseline, width, height]);

  const last = values[values.length - 1] ?? baseline;
  const stroke = last >= baseline ? colors.up : colors.down;

  return (
    <View style={{width, height}}>
      <Svg width={width} height={height}>
        <Line
          x1={0}
          y1={baselineY}
          x2={width}
          y2={baselineY}
          stroke={colors.border}
          strokeWidth={1}
          strokeDasharray="3,3"
        />
        {path ? <Path d={path} stroke={stroke} strokeWidth={2} fill="none" /> : null}
      </Svg>
    </View>
  );
}
