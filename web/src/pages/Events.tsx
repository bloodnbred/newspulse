import Placeholder from './Placeholder'

/** Event Lab. Built in a later UI phase; see docs/UI-BLUEPRINT.md.
 *
 *  Split from the main bundle by `React.lazy` in App.tsx, so this module is its
 *  own chunk. The libraries this page will need - React Flow, dnd-kit, cmdk -
 *  are therefore not on the path to the homepage. */
export default function Events() {
  return <Placeholder title="Event Lab" phase="later" />
}
