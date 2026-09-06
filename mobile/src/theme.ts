import type {TextStyle} from 'react-native';

/**
 * Design tokens.
 *
 * Dark-first: a trading screen is looked at in the dark far more than a banking
 * app, and a bright white P&L screen at 2am is a complaint waiting to happen.
 *
 * Gains are green and losses red *and also* carry an explicit sign everywhere,
 * because ~8% of men have a red/green colour-vision deficiency and a number
 * that only means something by hue is unreadable to them.
 */

export const colors = {
  background: '#0B0E14',
  surface: '#141922',
  surfaceRaised: '#1C2330',
  border: '#232C3B',
  text: '#E6EAF2',
  textMuted: '#8C97AB',
  textFaint: '#5A6478',
  up: '#26C281',
  down: '#F0616D',
  upWash: 'rgba(38,194,129,0.16)',
  downWash: 'rgba(240,97,109,0.16)',
  accent: '#4C8DFF',
  warning: '#F5A623',
  stale: '#6B7280',
} as const;

export const spacing = {xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32} as const;

export const radius = {sm: 6, md: 10, lg: 16} as const;

export const typography = {
  // Tabular figures are non-negotiable on a 1 Hz ticker: without them every
  // digit change shifts the layout and the number appears to vibrate.
  mono: {fontVariant: ['tabular-nums']} as TextStyle,
  display: {fontSize: 34, fontWeight: '700', letterSpacing: -0.5} as TextStyle,
  title: {fontSize: 20, fontWeight: '600'} as TextStyle,
  body: {fontSize: 15, fontWeight: '500'} as TextStyle,
  caption: {fontSize: 12, fontWeight: '500'} as TextStyle,
};

export const pnlColor = (value: number): string =>
  value > 0 ? colors.up : value < 0 ? colors.down : colors.textMuted;
