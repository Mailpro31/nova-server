import type { Page } from "../shell";

// Page à construire (refonte de la console).
export const etat: Page = {
  id: "etat",
  path: "/etat",
  async render() {
    return { title: "État des services", body: '<p class="empty">À construire.</p>' };
  },
};
