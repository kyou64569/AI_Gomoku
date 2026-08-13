import React from 'react';

export default function Board({ board, onCellClick, disabled }: { board: number[][]; onCellClick?: (row: number, col: number) => void; disabled?: boolean }) {
  return (
    <div className="board-grid w-full max-w-[600px] mx-auto">
      {board.map((row, r) =>
        row.map((cell, c) => (
          <div
            key={`${r}-${c}`}
            className="board-cell"
            onClick={() => !disabled && onCellClick && onCellClick(r, c)}
          >
            {cell === 1 && <div className="stone stone-black" />}
            {cell === 2 && <div className="stone stone-white" />}
          </div>
        ))
      )}
    </div>
  );
}
