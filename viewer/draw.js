// FleetMind drawing code, shared by the replay viewer (viewer/index.html)
// and the live website (web/static/live.html).
//
// It only knows how to DRAW. Where the data comes from (a recording or the
// live WebSocket) is up to the page. Both use the same frame format:
//   layout: {height, width, walls, shelves, chargers, packing, docks}   (record.layout_data)
//   robot:  {id, pos, status, task, battery, carrying, order, target, path, repair}  (record.snapshot)
//
// Usage:
//   const map = FleetDraw.create(canvas);
//   map.resize(layout, widthInPixels);
//   map.drawStatic(layout);
//   map.drawBlocked(frame.blocked);
//   map.drawTrail(positions, robot, selected);
//   map.drawRobot(robot, row, col, {showRoute, selected});
//   map.robotAt(event, robots)   -> id of the clicked robot, or null

(() => {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  const COLOR = {
    idle: v("--idle"),
    to_pickup: v("--to-pickup"),
    to_dropoff: v("--to-dropoff"),
    to_home: v("--idle"),
    wait_charger: v("--charge"),
    to_charger: v("--charge"),
    charging: v("--charge"),
    error: v("--error"),
  };
  const TASK_LABEL = {
    idle: "Idle",
    to_home: "Going home",
    wait_charger: "Waiting for charger",
    to_pickup: "Going to shelf",
    to_dropoff: "Carrying",
    to_charger: "Going to charge",
    charging: "Charging",
  };
  const SHELF_TINTS = ["#e8eefc", "#e6f4ec", "#fdf1df", "#f3e8fb", "#fde8e8", "#e6f3f6"];

  function robotColor(robot) {
    if (robot.status === "error") return COLOR.error;
    return COLOR[robot.task] || COLOR.idle;
  }

  function create(canvas) {
    const ctx = canvas.getContext("2d");
    const map = { cell: 40 };
    const cx = (c) => c * map.cell + map.cell / 2;   // centre x of a column
    const cy = (r) => r * map.cell + map.cell / 2;   // centre y of a row

    map.resize = (L, width) => {
      const cell = map.cell = Math.max(18, Math.floor(width / L.width));
      const dpr = window.devicePixelRatio || 1;
      canvas.width = L.width * cell * dpr;
      canvas.height = L.height * cell * dpr;
      canvas.style.width = `${L.width * cell}px`;
      canvas.style.height = `${L.height * cell}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    map.drawStatic = (L) => {
      const cell = map.cell;
      ctx.fillStyle = "#f7f8fa";
      ctx.fillRect(0, 0, L.width * cell, L.height * cell);

      // grid lines
      ctx.strokeStyle = "#eceef2";
      ctx.lineWidth = 1;
      for (let c = 0; c <= L.width; c++) { ctx.beginPath(); ctx.moveTo(c * cell + .5, 0); ctx.lineTo(c * cell + .5, L.height * cell); ctx.stroke(); }
      for (let r = 0; r <= L.height; r++) { ctx.beginPath(); ctx.moveTo(0, r * cell + .5); ctx.lineTo(L.width * cell, r * cell + .5); ctx.stroke(); }

      // walls
      ctx.fillStyle = "#2b2f36";
      for (const [r, c] of L.walls) ctx.fillRect(c * cell, r * cell, cell, cell);

      // shelves: tint by row (A, B, ...), outline around each row, and on every
      // shelf cell its id (A1) and its item
      const groups = {};
      L.shelves.forEach((s) => {
        const g = s.id.replace(/\d+$/, "");
        (groups[g] = groups[g] || { cells: [], name: s.name }).cells.push(...s.cells);
      });
      Object.keys(groups).sort().forEach((g, i) => {
        const tint = SHELF_TINTS[i % SHELF_TINTS.length];
        const cells = groups[g].cells;
        ctx.fillStyle = tint;
        for (const [r, c] of cells) ctx.fillRect(c * cell + 1, r * cell + 1, cell - 1, cell - 1);
        const rows = cells.map((p) => p[0]), cols = cells.map((p) => p[1]);
        const r0 = Math.min(...rows), r1 = Math.max(...rows), c0 = Math.min(...cols), c1 = Math.max(...cols);
        ctx.strokeStyle = "#b9c3d3";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(c0 * cell + 1, r0 * cell + 1, (c1 - c0 + 1) * cell - 2, (r1 - r0 + 1) * cell - 2);
      });
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      for (const s of L.shelves) {
        for (const [r, c] of s.cells) {
          ctx.fillStyle = "#3b4252";
          ctx.font = `700 ${Math.round(cell * 0.3)}px system-ui, sans-serif`;
          ctx.fillText(s.id, cx(c), cy(r) - cell * 0.12);
          const item = (s.items && s.items[0]) || "";
          ctx.fillStyle = "#6b7280";
          let size = Math.round(cell * 0.2);
          ctx.font = `${size}px system-ui, sans-serif`;
          while (size > 7 && ctx.measureText(item).width > cell - 4) {
            size -= 1;
            ctx.font = `${size}px system-ui, sans-serif`;
          }
          ctx.fillText(item, cx(c), cy(r) + cell * 0.2);
        }
      }

      // stations
      station(L.chargers, "#fff3cf", "#d99a00", "C");
      station(L.packing, "#efe6fb", "#7b4fc9", "P");
      station(L.docks, "#e7eaef", "#5b6475", "D");
    };

    function station(cells, fill, ink, letter) {
      const cell = map.cell;
      for (const [r, c] of cells) {
        ctx.fillStyle = fill;
        ctx.fillRect(c * cell + 1, r * cell + 1, cell - 1, cell - 1);
        ctx.fillStyle = ink;
        ctx.font = `700 ${Math.round(cell * 0.42)}px system-ui, sans-serif`;
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(letter, cx(c), cy(r));
      }
    }

    map.drawBlocked = (blocked) => {
      const cell = map.cell;
      for (const [r, c] of blocked) {
        const x = c * cell, y = r * cell, p = cell * 0.22;
        ctx.fillStyle = "rgba(214,69,69,.12)";
        ctx.fillRect(x + 1, y + 1, cell - 1, cell - 1);
        ctx.strokeStyle = "#d64545";
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.moveTo(x + p, y + p); ctx.lineTo(x + cell - p, y + cell - p);
        ctx.moveTo(x + cell - p, y + p); ctx.lineTo(x + p, y + cell - p);
        ctx.stroke();
      }
    };

    // A fading line through the robot's last positions ([[row, col], ...], oldest first)
    map.drawTrail = (positions, robot, selected) => {
      if (!positions.length) return;
      ctx.strokeStyle = robotColor(robot);
      ctx.globalAlpha = selected ? 0.55 : 0.25;
      ctx.lineWidth = selected ? 4 : 3;
      ctx.lineCap = "round"; ctx.lineJoin = "round";
      ctx.beginPath();
      positions.forEach(([row, col], k) => {
        if (k === 0) ctx.moveTo(cx(col), cy(row)); else ctx.lineTo(cx(col), cy(row));
      });
      ctx.stroke();
      ctx.globalAlpha = 1;
    };

    // One robot at (row, col). row/col can be fractional while it slides between cells.
    map.drawRobot = (robot, row, col, { showRoute = true, selected = false } = {}) => {
      const cell = map.cell;
      const x = cx(col), y = cy(row);
      const color = robotColor(robot);
      const radius = cell * 0.34;

      // planned route (A*): a dashed line through every cell of robot.path
      if (showRoute && robot.target) {
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = color;
        ctx.globalAlpha = 0.7;
        ctx.lineWidth = 2;
        ctx.lineCap = "round"; ctx.lineJoin = "round";
        ctx.beginPath();
        ctx.moveTo(x, y);
        const route = robot.path && robot.path.length ? robot.path : [robot.target];
        for (const [pr, pc] of route) ctx.lineTo(cx(pc), cy(pr));
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.lineWidth = 1.5;
        ctx.strokeRect(robot.target[1] * cell + 4, robot.target[0] * cell + 4, cell - 8, cell - 8);
        ctx.globalAlpha = 1;
      }

      // selection halo
      if (selected) {
        ctx.fillStyle = "rgba(47,111,223,.15)";
        ctx.beginPath(); ctx.arc(x, y, radius + 9, 0, Math.PI * 2); ctx.fill();
      }

      // battery ring
      ctx.lineWidth = 3;
      ctx.strokeStyle = "#e3e6eb";
      ctx.beginPath(); ctx.arc(x, y, radius + 3.5, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = robot.battery < 25 ? COLOR.error : "#5b6475";
      ctx.beginPath();
      ctx.arc(x, y, radius + 3.5, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * robot.battery / 100);
      ctx.stroke();

      // body
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.fill();

      // carried item: small white square
      if (robot.carrying) {
        ctx.fillStyle = "#fff";
        const s = radius * 0.5;
        ctx.fillRect(x + radius * 0.25, y - radius * 0.95, s, s);
        ctx.strokeStyle = color; ctx.lineWidth = 1.5;
        ctx.strokeRect(x + radius * 0.25, y - radius * 0.95, s, s);
      }

      // id number
      ctx.fillStyle = "#fff";
      ctx.font = `700 ${Math.round(cell * 0.32)}px system-ui, sans-serif`;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(robot.id.replace(/\D/g, ""), x, y + 1);
    };

    // Which robot did the user click? (null if none)
    map.robotAt = (event, robots) => {
      const rect = canvas.getBoundingClientRect();
      const col = (event.clientX - rect.left) / map.cell - 0.5;
      const row = (event.clientY - rect.top) / map.cell - 0.5;
      let best = null, bestD = 0.6;
      for (const r of robots) {
        const d = Math.hypot(r.pos[0] - row, r.pos[1] - col);
        if (d < bestD) { best = r.id; bestD = d; }
      }
      return best;
    };

    return map;
  }

  window.FleetDraw = { create, robotColor, COLOR, TASK_LABEL };
})();
