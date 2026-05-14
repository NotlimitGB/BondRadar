/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#17202a",
        panel: "#f6f8fb",
        line: "#d9e1ec",
        accent: "#0f766e",
      },
      boxShadow: {
        soft: "0 10px 28px rgba(23, 32, 42, 0.08)",
      },
    },
  },
  plugins: [],
};
