import type { Page } from "../shell";

// Page à construire (refonte de la console).
export const usage: Page = {
  id: "usage",
  path: "/usage",
  async render() {
    return { title: "Usage", body: '<p class="empty">À construire.</p>' };
  },
};
