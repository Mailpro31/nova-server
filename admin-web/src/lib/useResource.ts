/**
 * Chargement d'une ressource, avec ses quatre états.
 *
 * Chaque page l'utilise, ce qui garantit qu'aucune n'oublie « en cours » ni
 * « en échec » : c'est ce qui produit les écrans blancs.
 */

import { useCallback, useEffect, useState } from "react";
import { useSession } from "./useAdminSession";

export interface Resource<T> {
  status: "loading" | "error" | "ready";
  data?: T;
  error?: string;
}

export function useResource<T>(
  load: (() => Promise<T>) | null,
  deps: unknown[] = [],
): [Resource<T>, () => void] {
  const { reportFailure } = useSession();
  const [state, setState] = useState<Resource<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    if (!load) return;
    let alive = true;
    setState({ status: "loading" });
    load()
      .then((data) => {
        if (alive) setState({ status: "ready", data });
      })
      .catch((raised) => {
        // `reportFailure` fait aussi basculer la session si le refus l'exige —
        // session expirée, réauthentification requise.
        const message = reportFailure(raised);
        if (alive) setState({ status: "error", error: message });
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt, ...deps]);

  return [state, retry];
}
