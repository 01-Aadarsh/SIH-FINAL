import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        paper: "#FBF8F1",
        ink: "#221E17",
        forest: {
          50: "#EEF4EF",
          100: "#D6E5D9",
          300: "#7FAE8B",
          500: "#2F6B45",
          600: "#245536",
          700: "#1B3F28",
          900: "#12291A",
        },
        saffron: {
          100: "#FBEFD3",
          300: "#EDCB7C",
          500: "#C9962E",
          600: "#A97A1C",
        },
        clay: {
          50: "#F4EDE4",
          200: "#E4D5C2",
        },
        neu: {
          bg: "#EADFCB",
          surface: "#F8F1E4",
          text: "#3D3427",
          sub: "#96876F",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "sans-serif",
        ],
        serif: ["\"Source Serif 4\"", "ui-serif", "Georgia", "serif"],
      },
      boxShadow: {
        panel: "0 1px 2px rgba(34, 30, 23, 0.06), 0 4px 16px rgba(34, 30, 23, 0.06)",
        neu: "9px 9px 18px rgba(150, 120, 80, 0.35), -9px -9px 18px rgba(255, 250, 240, 0.85)",
        neuSm: "4px 4px 8px rgba(150, 120, 80, 0.35), -4px -4px 8px rgba(255, 250, 240, 0.85)",
        neuInset: "inset 3px 3px 6px rgba(150, 120, 80, 0.3), inset -3px -3px 6px rgba(255, 250, 240, 0.7)",
      },
      keyframes: {
        pulseSoft: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.55" },
        },
        fadeIn: {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        pulseSoft: "pulseSoft 1.8s ease-in-out infinite",
        fadeIn: "fadeIn 0.25s ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
