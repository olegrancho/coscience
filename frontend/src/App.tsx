import { AppShell, Group, Title, Anchor } from "@mantine/core";
import { Link, Route, Routes } from "react-router-dom";
import ProgramsOverview from "./views/ProgramsOverview";
import ProgramDetail from "./views/ProgramDetail";
import SprintDetail from "./views/SprintDetail";
import ResultDetail from "./views/ResultDetail";
import Ledger from "./views/Ledger";

export default function App() {
  return (
    <AppShell header={{ height: 56 }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Anchor component={Link} to="/" underline="never">
            <Title order={4}>Co-Science — Oversight</Title>
          </Anchor>
          <Anchor component={Link} to="/ledger">Ledger</Anchor>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <Routes>
          <Route path="/" element={<ProgramsOverview />} />
          <Route path="/programs/:id" element={<ProgramDetail />} />
          <Route path="/sprints/:id" element={<SprintDetail />} />
          <Route path="/results/:id" element={<ResultDetail />} />
          <Route path="/ledger" element={<Ledger />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}
