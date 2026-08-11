import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../..", import.meta.url));

export default {
  root,
  test: {
    environment: "node",
    include: ["tests/xingjing_commercial_client/**/*.test.ts"],
    testTimeout: 15_000,
  },
};
