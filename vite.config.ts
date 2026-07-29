import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      // Python 가상환경·모델·Rust 산출물은 UI 개발 서버가 감시할 대상이 아니다.
      ignored: [
        "**/backend/.venv/**",
        "**/backend/**/__pycache__/**",
        "**/backend/model/**",
        "**/src-tauri/target/**",
        "**/dist/**",
      ],
    },
  },
});
