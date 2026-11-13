// Registre des pages, dans l'ordre de la barre latérale (voir NAV dans shell.ts).
import type { Page } from "../shell";
import { accueil } from "./accueil";
import { claude } from "./claude";
import { ecoles } from "./ecoles";
import { usage } from "./usage";
import { ia } from "./ia";
import { signalements } from "./signalements";
import { licences } from "./licences";
import { activite } from "./activite";
import { etat } from "./etat";

export const PAGES: Page[] = [accueil, claude, ecoles, usage, ia, signalements, licences, activite, etat];
