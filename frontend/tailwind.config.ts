import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#070b17", 900: "#0b1120", 850: "#0f162a", 800: "#131c34", 700: "#1c2745", 600: "#28365c", 500: "#3a4a74" },
        fog: { 100: "#e8ecf6", 300: "#b6bfd6", 400: "#8d97b3", 500: "#6b7594" },
        violet: { 300: "#c4b5fd", 400: "#a78bfa", 500: "#8b5cf6", 600: "#7c3aed" },
        sky: { 400: "#60a5fa" },
        good: "#34d399", warn: "#fbbf24", bad: "#f87171",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      boxShadow: { card: "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 8px 24px -12px rgba(0,0,0,0.6)" },
    },
  },
  plugins: [],
} satisfies Config;
