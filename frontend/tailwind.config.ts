import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: "#17201e",
        canvas: "#f7f8f4",
        brand: {
          50: "#eef8f1",
          100: "#d9efdf",
          500: "#328156",
          600: "#246b45",
          700: "#1d5639",
        },
      },
      boxShadow: {
        card: "0 1px 2px rgba(20, 40, 32, 0.04), 0 8px 24px rgba(20, 40, 32, 0.05)",
      },
    },
  },
  plugins: [],
};

export default config;
