import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// L'origine de l'API est fournie au build ou à l'exécution du serveur de
// développement. Aucune adresse réelle n'est écrite ici : une URL privée
// codée en dur finirait dans le dépôt, puis dans un bundle de production.
export default defineConfig({
  plugins: [react()],
  // `127.0.0.1` litteralement, et non `localhost` : le serveur Nova ecarte le
  // nom d'hote pour les redirections d'autorisation — sa resolution ne se
  // maitrise pas — et Entra compare l'URI de redirection telle quelle.
  server: { host: "127.0.0.1", port: 5174, strictPort: true },
  build: { outDir: "dist", sourcemap: false },
});
