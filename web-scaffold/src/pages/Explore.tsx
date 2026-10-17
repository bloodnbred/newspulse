import Placeholder from './Placeholder'

/** Cube Studio. Built in a later UI phase; see docs/UI-BLUEPRINT.md.
 *
 *  Split from the main bundle by `React.lazy` in App.tsx, so this module is its
 *  own chunk. The libraries this page will need - React Flow, dnd-kit, cmdk -
 *  are therefore not on the path to the homepage. */
export default function Explore() {
  return <Placeholder title="Cube Studio" phase="later" />
}
