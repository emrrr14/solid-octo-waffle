import React, {useEffect, useRef} from 'react';
import {Animated, StyleSheet, Text, type TextStyle} from 'react-native';

import {colors, typography} from '../theme';

/**
 * A number that flashes when it changes.
 *
 * The flash is an opacity animation on a background layer with
 * `useNativeDriver: true`, so it runs on the UI thread and keeps its 60fps even
 * while JS is busy folding the next frame.  (Animating `backgroundColor`
 * directly would force the JS driver - smooth until the moment it matters.)
 *
 * Flashes are also *suppressed while scrolling* by the parent list: a wall of
 * blinking rows under a moving finger reads as broken, not as live.
 */
interface Props {
  value: number;
  render: (value: number) => string;
  direction: 'up' | 'down' | 'flat';
  style?: TextStyle | TextStyle[];
  flash?: boolean;
}

export const ValueTicker = React.memo(function ValueTicker({
  value,
  render,
  direction,
  style,
  flash = true,
}: Props) {
  const opacity = useRef(new Animated.Value(0)).current;
  const previous = useRef(value);

  useEffect(() => {
    if (!flash || value === previous.current || direction === 'flat') {
      previous.current = value;
      return;
    }
    previous.current = value;
    opacity.setValue(1);
    Animated.timing(opacity, {
      toValue: 0,
      duration: 420,
      useNativeDriver: true,
    }).start();
  }, [value, direction, flash, opacity]);

  return (
    <Animated.View style={styles.wrapper}>
      <Animated.View
        pointerEvents="none"
        style={[
          StyleSheet.absoluteFill,
          styles.flash,
          {
            opacity,
            backgroundColor: direction === 'up' ? colors.upWash : colors.downWash,
          },
        ]}
      />
      <Text style={[styles.text, style]} numberOfLines={1}>
        {render(value)}
      </Text>
    </Animated.View>
  );
});

const styles = StyleSheet.create({
  wrapper: {alignSelf: 'flex-end', paddingHorizontal: 4, borderRadius: 4},
  flash: {borderRadius: 4},
  text: {color: colors.text, ...typography.mono},
});
