import React from 'react';
import { BrowserRouter, Routes, Route, Link } from 'react-router-dom';
import Home from './pages/Home';
import ConfigPage from './pages/ConfigPage';
import RoomPage from './pages/RoomPage';
import GamePage from './pages/GamePage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/config" element={<ConfigPage />} />
        <Route path="/room/:roomId" element={<RoomPage />} />
        <Route path="/game/:gameId" element={<GamePage />} />
      </Routes>
    </BrowserRouter>
  );
}
