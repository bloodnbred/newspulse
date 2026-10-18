import { Link } from 'react-router-dom';
import { Compass } from 'lucide-react';

/** Shown for any URL the router does not recognise.
 *
 *  Without a catch-all route, a mistyped or stale link renders the shell with
 *  nothing in it: the navigation loads, the footer loads, and the middle of the
 *  page is blank. A reader cannot tell that from a page still fetching, so the
 *  honest thing is to say the address is not one this app serves and offer the
 *  way back.
 *
 *  `PlaceHolder` from the phase pack is deliberately not used here: it tells
 *  the reader a page is "built in phase U4", which is a note to the developers
 *  and not an answer for the person who followed a bad link. */
export default function NotFound() {
  return (
    <div className="glass p-10 mt-4 flex flex-col items-start gap-4">
      <Compass size={28} style={{ color: 'var(--news)' }} aria-hidden="true" />
      <div>
        <h1 className="text-2xl font-semibold">There is no page at this address</h1>
        <p className="text-sm mt-2 max-w-prose" style={{ color: 'var(--muted)' }}>
          The link may be mistyped, or from an older version of this app. Everything
          the app can show is listed in the navigation on the left.
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Link to="/" className="btn">Back to the dashboard</Link>
        <Link to="/explore" className="btn">Explore the data</Link>
      </div>
    </div>
  );
}