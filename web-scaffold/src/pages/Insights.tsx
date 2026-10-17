import Placeholder from './Placeholder'

/** Insights. Built in a later UI phase; see docs/UI-BLUEPRINT.md.
 *
 *  Split from the main bundle by `React.lazy` in App.tsx, so this module is its
 *  own chunk. The libraries this page will need - React Flow, dnd-kit, cmdk -
 *  are therefore not on the path to the homepage. */
export default function Insights() {
  return <Placeholder title="Insights" phase="later" />
}
