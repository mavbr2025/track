export const GRID_WIDTH = 18;
export const GRID_HEIGHT = 18;
export const TICK_MS = 140;

export const DIRECTION_VECTORS = {
  up: { x: 0, y: -1 },
  right: { x: 1, y: 0 },
  down: { x: 0, y: 1 },
  left: { x: -1, y: 0 },
};

const OPPOSITE_DIRECTION = {
  up: "down",
  right: "left",
  down: "up",
  left: "right",
};

const positionKey = ({ x, y }) => `${x},${y}`;

export const positionsEqual = (a, b) => a.x === b.x && a.y === b.y;

export const isInsideGrid = (position, width = GRID_WIDTH, height = GRID_HEIGHT) =>
  position.x >= 0 && position.x < width && position.y >= 0 && position.y < height;

export const normalizeDirection = (value) => {
  if (typeof value !== "string") return null;
  const key = value.toLowerCase();
  if (key in DIRECTION_VECTORS) return key;
  return null;
};

export const queueDirection = (currentDirection, requestedDirection) => {
  const current = normalizeDirection(currentDirection);
  const requested = normalizeDirection(requestedDirection);
  if (!current || !requested) return current;
  if (OPPOSITE_DIRECTION[current] === requested) return current;
  return requested;
};

export const createInitialSnake = (width = GRID_WIDTH, height = GRID_HEIGHT) => {
  const centerX = Math.floor(width / 2);
  const centerY = Math.floor(height / 2);
  return [
    { x: centerX, y: centerY },
    { x: centerX - 1, y: centerY },
    { x: centerX - 2, y: centerY },
  ];
};

export const spawnFood = (
  snake,
  width = GRID_WIDTH,
  height = GRID_HEIGHT,
  randomFn = Math.random,
) => {
  const occupied = new Set(snake.map(positionKey));
  const freeCells = [];

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const key = `${x},${y}`;
      if (!occupied.has(key)) freeCells.push({ x, y });
    }
  }

  if (freeCells.length === 0) return null;

  const randomValue = Number(randomFn());
  const bounded = Number.isFinite(randomValue) ? Math.min(Math.max(randomValue, 0), 0.999999) : 0;
  const index = Math.floor(bounded * freeCells.length);
  return freeCells[index];
};

export const createInitialState = (
  width = GRID_WIDTH,
  height = GRID_HEIGHT,
  randomFn = Math.random,
) => {
  const snake = createInitialSnake(width, height);
  return {
    width,
    height,
    snake,
    direction: "right",
    pendingDirection: "right",
    food: spawnFood(snake, width, height, randomFn),
    score: 0,
    isPaused: false,
    isGameOver: false,
  };
};

export const stepGame = (state, randomFn = Math.random) => {
  if (state.isGameOver || state.isPaused) return state;

  const direction = queueDirection(state.direction, state.pendingDirection);
  const vector = DIRECTION_VECTORS[direction];
  const currentHead = state.snake[0];
  const nextHead = {
    x: currentHead.x + vector.x,
    y: currentHead.y + vector.y,
  };

  if (!isInsideGrid(nextHead, state.width, state.height)) {
    return { ...state, direction, isGameOver: true };
  }

  const eatsFood = state.food && positionsEqual(nextHead, state.food);
  const collisionBody = eatsFood ? state.snake : state.snake.slice(0, -1);
  const hitsSelf = collisionBody.some((segment) => positionsEqual(segment, nextHead));

  if (hitsSelf) {
    return { ...state, direction, isGameOver: true };
  }

  const snake = [nextHead, ...state.snake];
  if (!eatsFood) snake.pop();

  let food = state.food;
  let score = state.score;
  let isGameOver = false;

  if (eatsFood) {
    score += 1;
    food = spawnFood(snake, state.width, state.height, randomFn);
    if (!food) isGameOver = true;
  }

  return {
    ...state,
    snake,
    direction,
    pendingDirection: direction,
    food,
    score,
    isGameOver,
  };
};

export const setPendingDirection = (state, requestedDirection) => ({
  ...state,
  // Allow only one direction change per tick to avoid impossible double-turns.
  pendingDirection:
    state.pendingDirection !== state.direction
      ? state.pendingDirection
      : queueDirection(state.direction, requestedDirection),
});

export const togglePause = (state) => {
  if (state.isGameOver) return state;
  return { ...state, isPaused: !state.isPaused };
};
