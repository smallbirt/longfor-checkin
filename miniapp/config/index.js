const { merge } = require("webpack-merge");

const config = {
  projectName: "daily-checkin-weapp",
  date: "2026-03-16",
  designWidth: 750,
  deviceRatio: {
    640: 2.34 / 2,
    750: 1,
    828: 1.81 / 2
  },
  sourceRoot: "src-checkin",
  outputRoot: "dist",
  framework: "react",
  compiler: {
    type: "webpack5"
  },
  copy: {
    patterns: [
      {
        from: "src-checkin/assets",
        to: "dist/assets"
      },
      {
        from: "project.config.json",
        to: "dist/project.config.json"
      }
    ],
    options: {}
  },
  cache: {
    enable: false
  },
  defineConstants: {
    __CHECKIN_API_BASE_URL__: JSON.stringify(process.env.CHECKIN_API_BASE_URL || "https://fitlean-api.mingyuefanxing.top")
  },
  mini: {},
  h5: {}
};

module.exports = function (mergeFn = merge) {
  if (process.env.NODE_ENV === "development") {
    return mergeFn({}, config, require("./dev"));
  }
  return mergeFn({}, config, require("./prod"));
};
