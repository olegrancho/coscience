import { createTheme, type MantineColorsTuple } from "@mantine/core";

// "machine" teal — the calm colour of autonomous activity.
const machine: MantineColorsTuple = [
  "#e3f7f3", "#cdeee8", "#a3ddd3", "#76ccbd", "#52bdab",
  "#3bb4a0", "#2cae98", "#1b9a85", "#0c8975", "#00755f",
];

// "signal" amber — reserved for the one thing that wants a human: a decision.
const signal: MantineColorsTuple = [
  "#fdf1e0", "#f7e0c4", "#efc795", "#e7ad63", "#e1973a",
  "#dd8b22", "#dc8417", "#c3700d", "#ad6207", "#964f00",
];

export const theme = createTheme({
  primaryColor: "machine",
  primaryShade: 7,
  fontFamily: "'IBM Plex Sans Variable', system-ui, sans-serif",
  fontFamilyMonospace: "'IBM Plex Mono', ui-monospace, monospace",
  headings: {
    fontFamily: "'Space Grotesk', system-ui, sans-serif",
    fontWeight: "600",
  },
  defaultRadius: "md",
  colors: { machine, signal },
  white: "#ffffff",
  black: "#1a2622",
});
