import type { Page } from "../shell";

// Page à construire (refonte de la console).
export const signalements: Page = {
  id: "signalements",
  path: "/signalements",
  async render() {
    return { title: "Signalements", body: '<p class="empty">À construire.</p>' };
  },
};
