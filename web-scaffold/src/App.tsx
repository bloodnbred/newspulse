import { Route, Routes } from 'react-router-dom';
import { AppShell } from './components/AppShell';
import Home from './pages/Home';
import Placeholder from './pages/Placeholder';

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Home />} />
        <Route path="explore" element={<Placeholder title="Cube Studio" phase="U4" />} />
        <Route path="stocks" element={<Placeholder title="Stock 360" phase="U5" />} />
        <Route path="stocks/:symbol" element={<Placeholder title="Stock 360" phase="U5" />} />
        <Route path="events" element={<Placeholder title="Event Lab" phase="U5" />} />
        <Route path="mining" element={<Placeholder title="Mining Lab" phase="U6" />} />
        <Route path="headline" element={<Placeholder title="Headline Lab" phase="U6" />} />
        <Route path="data" element={<Placeholder title="Data & Schema" phase="U7" />} />
        <Route path="insights" element={<Placeholder title="Insights" phase="U7" />} />
      </Route>
    </Routes>
  );
}
