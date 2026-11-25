/** @type {import("tailwindcss").Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: { 900: "#0a0e1a", 800: "#111827", 700: "#1f2937", 600: "#374151" },
        accent: { 500: "#3b82f6", 400: "#60a5fa" },
      },
      keyframes: {
        dash: {
          "0%": { strokeDashoffset: "24" },
          "100%": { strokeDashoffset: "0" },
        },
        bounce: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-10px)" },
        }
      },
    },
  },
  plugins: [],
};