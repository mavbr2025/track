import {
  createInitialState,
  DIRECTION_VECTORS,
  GRID_HEIGHT,
  GRID_WIDTH,
  setPendingDirection,
  stepGame,
  TICK_MS,
  togglePause,
} from "./snake-logic.mjs";

const metricNodes = document.querySelectorAll("[data-target]");

const animateValue = (el, target, duration = 1200) => {
  const start = 0;
  const startTime = performance.now();

  const tick = (time) => {
    const progress = Math.min((time - startTime) / duration, 1);
    const eased = 1 - (1 - progress) ** 3;
    const value = Math.floor(start + (target - start) * eased);
    el.textContent = value.toString();
    if (progress < 1) requestAnimationFrame(tick);
  };

  requestAnimationFrame(tick);
};

metricNodes.forEach((node) => {
  const target = Number(node.getAttribute("data-target"));
  if (Number.isFinite(target)) animateValue(node, target);
});

const yearNode = document.getElementById("year");
if (yearNode) yearNode.textContent = new Date().getFullYear().toString();

const boardNode = document.getElementById("snake-board");
const scoreNode = document.getElementById("snake-score");
const statusNode = document.getElementById("snake-status");
const pauseButton = document.getElementById("snake-pause");
const restartButton = document.getElementById("snake-restart");
const controlButtons = document.querySelectorAll("[data-direction]");

const keyDirectionMap = {
  ArrowUp: "up",
  ArrowRight: "right",
  ArrowDown: "down",
  ArrowLeft: "left",
  w: "up",
  d: "right",
  s: "down",
  a: "left",
};

const cellIndex = (x, y) => y * GRID_WIDTH + x;

if (boardNode && scoreNode && statusNode && pauseButton && restartButton) {
  let state = createInitialState();
  const cells = [];

  boardNode.style.setProperty("--grid-width", GRID_WIDTH.toString());
  boardNode.style.setProperty("--grid-height", GRID_HEIGHT.toString());

  for (let i = 0; i < GRID_WIDTH * GRID_HEIGHT; i += 1) {
    const cell = document.createElement("div");
    cell.className = "snake-cell";
    boardNode.appendChild(cell);
    cells.push(cell);
  }

  const updateMeta = () => {
    scoreNode.textContent = state.score.toString();

    if (state.isGameOver) {
      statusNode.textContent = "Game over";
      pauseButton.textContent = "Pause";
      pauseButton.disabled = true;
    } else if (state.isPaused) {
      statusNode.textContent = "Paused";
      pauseButton.textContent = "Resume";
      pauseButton.disabled = false;
    } else {
      statusNode.textContent = "Running";
      pauseButton.textContent = "Pause";
      pauseButton.disabled = false;
    }
  };

  const render = () => {
    for (const cell of cells) {
      cell.classList.remove("snake-head", "snake-body", "snake-food");
    }

    if (state.food) {
      const foodCell = cells[cellIndex(state.food.x, state.food.y)];
      if (foodCell) foodCell.classList.add("snake-food");
    }

    state.snake.forEach((segment, index) => {
      const cell = cells[cellIndex(segment.x, segment.y)];
      if (!cell) return;
      cell.classList.add(index === 0 ? "snake-head" : "snake-body");
    });

    updateMeta();
  };

  const requestDirection = (direction) => {
    if (!(direction in DIRECTION_VECTORS) || state.isGameOver) return;
    state = setPendingDirection(state, direction);
    render();
  };

  const resetGame = () => {
    state = createInitialState();
    render();
  };

  pauseButton.addEventListener("click", () => {
    state = togglePause(state);
    render();
  });

  restartButton.addEventListener("click", resetGame);

  controlButtons.forEach((button) => {
    button.addEventListener("click", () => {
      requestDirection(button.getAttribute("data-direction"));
    });
  });

  document.addEventListener("keydown", (event) => {
    const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
    const mappedDirection = keyDirectionMap[key];
    if (mappedDirection) {
      event.preventDefault();
      requestDirection(mappedDirection);
      return;
    }

    if (event.code === "Space") {
      event.preventDefault();
      state = togglePause(state);
      render();
    }
  });

  window.setInterval(() => {
    state = stepGame(state);
    render();
  }, TICK_MS);

  render();
}
