import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';

/** Go back within the app, or forward when there is nothing to go back to.
 *
 *  `navigate(-1)` on its own is a trap in a deep-linked app. These pages are
 *  reachable by URL from the command palette, from a shared link and from
 *  search, so the reader may well have arrived with no in-app history at all -
 *  and `navigate(-1)` then takes them out of the application entirely, to
 *  wherever the browser happened to be before, or nowhere.
 *
 *  Checking `window.history.state.idx` is how React Router 6 records how far
 *  into the session's own history stack the reader is. `0` means this document
 *  is where the history begins, so there is no previous entry to go back to and
 *  the honest fallback is a known-good route rather than leaving the site.
 *
 *  `fallback` is per call site because the two pages have different sensible
 *  homes: a company page came from wherever the reader was browsing, while the
 *  event list is a page in its own right. */
export function useSafeBack(fallback: string) {
  const navigate = useNavigate();

  return useCallback(() => {
    const state = window.history.state as { idx?: number } | null;
    const index = typeof state?.idx === 'number' ? state.idx : null;
    // An index of 0 is the first entry; anything above it has something before
    // it. A null index means the state is not React Router's - browser Back
    // restored a page from bfcache with state we do not recognise - and going
    // back is still the right guess there.
    if (index === null || index > 0) navigate(-1);
    else navigate(fallback);
  }, [navigate, fallback]);
}