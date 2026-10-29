import React, { useEffect, useRef, useState, useCallback } from "react";
import * as THREE from "three";

// -------------------------------------------------------------
// NAVIGATION GRAPH & WAYPOINTS FOR ISOMETRIC TOY-BOX STUDIO
// -------------------------------------------------------------
const NAV_NODES = {
  // Central Corridor Hubs (Obstacle-free spine)
  hubCenter: { x: 0.8, z: 0.5, neighbors: ["hubNW", "hubSW", "hubNE", "hubSE", "work_pickup", "work_conveyor"] },
  hubNW: { x: -2.2, z: 0.2, neighbors: ["hubCenter", "hubSW", "work_table", "work_notes", "work_stamp", "work_mcp"] },
  hubSW: { x: -2.0, z: 2.2, neighbors: ["hubCenter", "hubNW", "camp_fire", "camp_stream"] },
  hubNE: { x: 2.6, z: -1.5, neighbors: ["hubCenter", "hubSE", "gym_treadmill", "gym_vending"] },
  hubSE: { x: 2.6, z: 1.8, neighbors: ["hubCenter", "hubNE", "gym_dumbbell"] },

  // Station Waypoints
  work_table: { x: -4.8, z: -2.0, face: { x: -3.8, z: -0.8 }, neighbors: ["hubNW"] },
  work_notes: { x: -5.8, z: -2.2, face: { x: -7.6, z: -2.2 }, neighbors: ["hubNW"] },
  work_jar: { x: -5.3, z: -4.4, face: { x: -6.8, z: -5.2 }, neighbors: ["work_mcp"] },
  work_mcp: { x: -2.5, z: -4.5, face: { x: -2.5, z: -6.0 }, neighbors: ["hubNW", "work_jar"] },
  work_conveyor: { x: -1.1, z: -3.2, face: { x: 0.4, z: -3.2 }, neighbors: ["hubCenter"] },
  work_pickup: { x: 0.4, z: -1.2, face: { x: 0.4, z: -3.2 }, neighbors: ["hubCenter"] },
  work_stamp: { x: -3.6, z: -1.2, face: { x: -4.0, z: -1.4 }, neighbors: ["hubNW"] },

  camp_fire: { x: -2.8, z: 3.8, face: { x: -4.0, z: 4.0 }, neighbors: ["hubSW"] },
  camp_stream: { x: -4.6, z: 2.6, face: { x: -4.0, z: 2.6 }, neighbors: ["hubSW", "camp_tent"] },
  camp_tent: { x: -5.2, z: 4.5, face: { x: -6.2, z: 4.5 }, neighbors: ["camp_stream"] },

  gym_treadmill: { x: 5.8, z: -2.8, face: { x: 5.8, z: -5.0 }, neighbors: ["hubNE"] },
  gym_dumbbell: { x: 4.6, z: 2.2, face: { x: 4.6, z: 5.0 }, neighbors: ["hubSE"] },
  gym_vending: { x: 5.4, z: -4.2, face: { x: 6.2, z: -5.2 }, neighbors: ["hubNE"] },
};

// BFS Shortest Path between graph nodes
function findPath(startKey, targetKey) {
  if (startKey === targetKey) return [targetKey];
  const queue = [[startKey]];
  const visited = new Set([startKey]);

  while (queue.length > 0) {
    const path = queue.shift();
    const node = path[path.length - 1];

    const neighbors = NAV_NODES[node]?.neighbors || [];
    for (const nb of neighbors) {
      if (nb === targetKey) {
        return [...path.slice(1), targetKey];
      }
      if (!visited.has(nb)) {
        visited.add(nb);
        queue.push([...path, nb]);
      }
    }
  }
  return [targetKey];
}

// Find nearest node to arbitrary coordinates (for click-to-walk)
function getNearestNode(x, z) {
  let best = "hubCenter";
  let minD = Infinity;
  for (const [key, n] of Object.entries(NAV_NODES)) {
    const d = (n.x - x) ** 2 + (n.z - z) ** 2;
    if (d < minD) {
      minD = d;
      best = key;
    }
  }
  return best;
}

// Chibi dialogue sets
const IDLE_ACTIVITIES = [
  { key: "camp_fire", bubble: "🔥 露营篝火烤火晃脚丫~ 随时等主公派活！", action: "sit_fire" },
  { key: "gym_treadmill", bubble: "🏃‍♀️ 胶囊跑步机有氧热身！心率 120 bpm~", action: "jog" },
  { key: "gym_dumbbell", bubble: "💪 嘿咻！举铁锻炼高并发推理核心！", action: "curls" },
  { key: "work_jar", bubble: "💎 哇，组织紫晶金库温润充盈~", action: "inspect_jar" },
  { key: "camp_stream", bubble: "🐟 荧光溪流里有闪烁的数据小鱼~", action: "peer_stream" },
  { key: "work_mcp", bubble: "📼 擦拭万界 MCP 卡带架，技能满满！", action: "inspect_mcp" },
  { key: "gym_vending", bubble: "🥤 贩卖机投币买罐荧光薄荷能量水~", action: "vending" },
];

export default function CyberStudio3D({
  stagesState = {},
  activeProjectId = "",
  totalRmb = 0,
  totalTokens = 0,
  crystalsCount = 0,
  budgetAlert80 = false,
  approvalModal = null,
  openStageDrawer = () => {},
  onOpenExchange = () => {},
  onOpenApproval = () => {},
  onDirectiveFocus = () => {},
  cameraPreset = "overview",
  onCameraPresetChange = () => {},
}) {
  const mountRef = useRef(null);

  // Floating bubble overlay state
  const [bubbleText, setBubbleText] = useState("🐾 赛博猫娘 Neko-01 就绪！点击地面寻路，或随时下达指令！");
  const [bubbleScreenPos, setBubbleScreenPos] = useState({ x: -999, y: -999, visible: false });
  const [showNekoPanel, setShowNekoPanel] = useState(false);
  const [hoveredPropName, setHoveredPropName] = useState(null);
  const [clickRipple, setClickRipple] = useState(null);

  // References to communicate with Three.js animation loop
  const runtimeRef = useRef({
    targetStation: "camp_fire",
    currentAction: "sit_fire",
    pathQueue: [],
    currentWaypoint: { x: -2.8, z: 3.8 },
    nekoPos: { x: -2.8, z: 3.8 },
    isWalking: false,
    walkSpeed: 4.2,
    manualInterruptUntil: 0,
    crystalInHand: false,
    cameraPreset: "overview",
    followNeko: false,
    stagesState: {},
    crystalsCount: 0,
    budgetAlert80: false,
    approvalModal: null,
  });

  // Sync state into runtimeRef
  useEffect(() => {
    runtimeRef.current.stagesState = stagesState;
    runtimeRef.current.crystalsCount = crystalsCount;
    runtimeRef.current.budgetAlert80 = budgetAlert80;
    runtimeRef.current.approvalModal = approvalModal;
    runtimeRef.current.cameraPreset = cameraPreset;
    runtimeRef.current.followNeko = cameraPreset === "follow";
  }, [stagesState, crystalsCount, budgetAlert80, approvalModal, cameraPreset]);

  // Command to order Neko-01 to a station
  const orderNekoTo = useCallback((stationKey, customBubble, customAction) => {
    const rt = runtimeRef.current;
    rt.targetStation = stationKey;
    rt.currentAction = customAction || "work";
    rt.manualInterruptUntil = Date.now() + 14000;

    const startNode = getNearestNode(rt.nekoPos.x, rt.nekoPos.z);
    rt.pathQueue = findPath(startNode, stationKey);
    rt.isWalking = true;
    if (customBubble) setBubbleText(customBubble);
  }, []);

  // React to stage changes (Interrupts idle life!)
  useEffect(() => {
    const st = stagesState;
    if (budgetAlert80 || approvalModal) {
      orderNekoTo("work_stamp", "🐾 主公请签章！合规红线安全挂起中！", "jump_stamp");
      return;
    }
    if (st.build === "active") {
      orderNekoTo("work_table", "💻 正在弧形全息工作台高速总装构建中...", "typing");
      return;
    }
    if (st.test === "active" || st.accept === "active") {
      orderNekoTo("work_conveyor", "🔍 脉冲激光质检扫描断言中，严防违规！", "qa");
      return;
    }
    if (st.intake === "active" || st.charter === "active" || st.workshop === "active" || st.wbs === "active") {
      orderNekoTo("work_notes", "📋 正在全息黑板拆解 WBS 与研讨立项...", "stick_notes");
      return;
    }
    if (st.release === "done" || st.retro === "active") {
      // Crystal delivery sequence!
      orderNekoTo("work_pickup", "📦 出货打包完毕！拾取组织避坑紫晶...", "pickup_crystal");
      setTimeout(() => {
        runtimeRef.current.crystalInHand = true;
        orderNekoTo("work_jar", "💎 正在将紫晶护送存入组织经验金库！", "deposit_crystal");
      }, 4000);
      return;
    }
  }, [stagesState, budgetAlert80, approvalModal, orderNekoTo]);

  // Main Three.js Lifecycle
  useEffect(() => {
    const container = mountRef.current;
    if (!container) return;

    const width = container.clientWidth || window.innerWidth;
    const height = container.clientHeight || (window.innerHeight - 56);

    // 1. Scene, Camera & Renderer
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x070b14);

    const aspect = width / height;
    const frustumSize = 11;
    const camera = new THREE.OrthographicCamera(
      (-frustumSize * aspect) / 2,
      (frustumSize * aspect) / 2,
      frustumSize / 2,
      -frustumSize / 2,
      -50,
      100
    );

    // Default Isometric Camera Position
    camera.position.set(20, 22, 20);
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance" });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.2;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.appendChild(renderer.domElement);

    // 2. Lighting System
    const ambientLight = new THREE.AmbientLight(0x0f172a, 1.8);
    scene.add(ambientLight);

    // Sun / Key Light
    const sunLight = new THREE.DirectionalLight(0xe0f2fe, 2.2);
    sunLight.position.set(16, 26, 12);
    sunLight.castShadow = true;
    sunLight.shadow.mapSize.width = 2048;
    sunLight.shadow.mapSize.height = 2048;
    sunLight.shadow.bias = -0.0005;
    sunLight.shadow.normalBias = 0.02;
    sunLight.shadow.camera.left = -14;
    sunLight.shadow.camera.right = 14;
    sunLight.shadow.camera.top = 14;
    sunLight.shadow.camera.bottom = -14;
    scene.add(sunLight);

    // Blue & Purple Rim Lights
    const blueRim = new THREE.DirectionalLight(0x38bdf8, 1.2);
    blueRim.position.set(-18, 12, -16);
    scene.add(blueRim);

    const purpleRim = new THREE.DirectionalLight(0xa855f7, 0.9);
    purpleRim.position.set(12, 8, -16);
    scene.add(purpleRim);

    // Interactive PointLights
    const deskLight = new THREE.PointLight(0x00f0ff, 1.8, 6);
    deskLight.position.set(-4.2, 1.8, -1.4);
    scene.add(deskLight);

    const jarLight = new THREE.PointLight(0xa855f7, 2.2, 5);
    jarLight.position.set(-6.8, 1.6, -5.2);
    scene.add(jarLight);

    const campLight = new THREE.PointLight(0x22d3ee, 2.0, 7);
    campLight.position.set(-4.0, 1.0, 4.0);
    scene.add(campLight);

    const gymLight = new THREE.PointLight(0xf43f5e, 1.6, 6);
    gymLight.position.set(5.2, 1.8, 0.5);
    scene.add(gymLight);

    // 3. DIORAMA PLATFORM BASE (18 x 1.2 x 14)
    const platformGroup = new THREE.Group();
    scene.add(platformGroup);

    // Dark Obsidian Rounded Platform Body
    const baseGeo = new THREE.BoxGeometry(18, 1.2, 14);
    const baseMat = new THREE.MeshStandardMaterial({
      color: 0x090e1a,
      roughness: 0.35,
      metalness: 0.25,
    });
    const baseMesh = new THREE.Mesh(baseGeo, baseMat);
    baseMesh.position.y = -0.6;
    baseMesh.receiveShadow = true;
    platformGroup.add(baseMesh);

    // Glowing Neon Perimeter Rim Strip
    const rimGeo = new THREE.BoxGeometry(18.15, 0.12, 14.15);
    const rimMat = new THREE.MeshBasicMaterial({ color: 0x00f0ff });
    const rimMesh = new THREE.Mesh(rimGeo, rimMat);
    rimMesh.position.y = -0.15;
    platformGroup.add(rimMesh);

    // 4. ZONE 1: 🛠️ 核心造物工坊区 (North-West)
    const workshopGroup = new THREE.Group();
    scene.add(workshopGroup);

    // Cutaway Back & Left Dark Cyber Walls
    const wallNorthGeo = new THREE.BoxGeometry(9.5, 3.4, 0.3);
    const wallWestGeo = new THREE.BoxGeometry(0.3, 3.4, 7.2);
    const wallMat = new THREE.MeshStandardMaterial({ color: 0x0d1527, roughness: 0.5, metalness: 0.2 });

    const wallNorth = new THREE.Mesh(wallNorthGeo, wallMat);
    wallNorth.position.set(-4.2, 1.7, -6.8);
    wallNorth.castShadow = true;
    wallNorth.receiveShadow = true;
    workshopGroup.add(wallNorth);

    const wallWest = new THREE.Mesh(wallWestGeo, wallMat);
    wallWest.position.set(-8.8, 1.7, -3.3);
    wallWest.castShadow = true;
    wallWest.receiveShadow = true;
    workshopGroup.add(wallWest);

    // Left Wall: 全息背投屏 + 3D 五彩便利贴墙 (Stages 01–04)
    const noteBoardFrame = new THREE.Mesh(
      new THREE.BoxGeometry(0.1, 2.2, 3.8),
      new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.2, metalness: 0.8 })
    );
    noteBoardFrame.position.set(-8.6, 1.8, -2.4);
    workshopGroup.add(noteBoardFrame);

    const noteHoloScreen = new THREE.Mesh(
      new THREE.PlaneGeometry(3.6, 2.0),
      new THREE.MeshStandardMaterial({ color: 0x00f0ff, transparent: true, opacity: 0.25, side: THREE.DoubleSide })
    );
    noteHoloScreen.rotation.y = Math.PI / 2;
    noteHoloScreen.position.set(-8.54, 1.8, -2.4);
    workshopGroup.add(noteHoloScreen);

    // 3D Glowing Colorful Sticky Notes
    const noteColors = [0xf43f5e, 0xfacc15, 0x10b981, 0x00f0ff, 0xa855f7, 0x38bdf8];
    const stickyNotes = [];
    for (let row = 0; row < 3; row++) {
      for (let col = 0; col < 4; col++) {
        const noteGeo = new THREE.PlaneGeometry(0.34, 0.34);
        const noteMat = new THREE.MeshStandardMaterial({
          color: noteColors[(row * 4 + col) % noteColors.length],
          emissive: noteColors[(row * 4 + col) % noteColors.length],
          emissiveIntensity: 0.4,
          roughness: 0.2,
        });
        const noteMesh = new THREE.Mesh(noteGeo, noteMat);
        noteMesh.rotation.y = Math.PI / 2;
        noteMesh.position.set(-8.52, 2.3 - row * 0.52, -3.6 + col * 0.76);
        noteMesh.name = "prop_notes";
        workshopGroup.add(noteMesh);
        stickyNotes.push(noteMesh);
      }
    }

    // Curved Frosted Holo-Desk (Stage 05 Build & Directive)
    const deskCurve = new THREE.CylinderGeometry(2.4, 2.4, 0.85, 32, 1, true, -Math.PI * 0.75, Math.PI * 0.8);
    const deskMat = new THREE.MeshStandardMaterial({
      color: 0x00f0ff,
      transparent: true,
      opacity: 0.45,
      roughness: 0.1,
      metalness: 0.1,
      side: THREE.DoubleSide,
    });
    const holoDesk = new THREE.Mesh(deskCurve, deskMat);
    holoDesk.position.set(-4.2, 0.42, -1.4);
    holoDesk.name = "prop_table";
    workshopGroup.add(holoDesk);

    // 3 Curved Floating Holo-Screens Above Desk
    const screensGroup = new THREE.Group();
    for (let s = -1; s <= 1; s++) {
      const scr = new THREE.Mesh(
        new THREE.PlaneGeometry(0.9, 0.55),
        new THREE.MeshBasicMaterial({ color: 0x00f0ff, transparent: true, opacity: 0.7, side: THREE.DoubleSide })
      );
      scr.position.set(s * 0.85, 0, -0.2 - Math.abs(s) * 0.15);
      scr.rotation.y = -s * 0.25;
      screensGroup.add(scr);
    }
    screensGroup.position.set(-4.2, 1.35, -1.2);
    screensGroup.rotation.y = 0.3;
    workshopGroup.add(screensGroup);

    // Glowing Cat-Paw Stamp on right corner of the desk
    const stampGroup = new THREE.Group();
    const stampBase = new THREE.Mesh(
      new THREE.CylinderGeometry(0.18, 0.22, 0.15, 16),
      new THREE.MeshStandardMaterial({ color: 0xff0055, emissive: 0xff0055, emissiveIntensity: 0.5 })
    );
    const stampPaw = new THREE.Mesh(
      new THREE.SphereGeometry(0.14, 16, 16),
      new THREE.MeshStandardMaterial({ color: 0xff88a5 })
    );
    stampPaw.position.y = 0.14;
    stampGroup.add(stampBase, stampPaw);
    stampGroup.position.set(-3.6, 0.9, -1.0);
    stampGroup.name = "prop_stamp";
    workshopGroup.add(stampGroup);

    // Glass Amethyst Crystal Cultivation Jar (Stage 09 Vault & FinOps)
    const jarGroup = new THREE.Group();
    jarGroup.position.set(-6.8, 0, -5.2);
    jarGroup.name = "prop_jar";
    workshopGroup.add(jarGroup);

    // Pedestal
    const jarPedestal = new THREE.Mesh(
      new THREE.CylinderGeometry(0.85, 0.95, 0.4, 8),
      new THREE.MeshStandardMaterial({ color: 0x1e1b4b, metalness: 0.8, roughness: 0.2 })
    );
    jarPedestal.position.y = 0.2;
    jarGroup.add(jarPedestal);

    // Shiny Glass Cylinder
    const jarGlass = new THREE.Mesh(
      new THREE.CylinderGeometry(0.72, 0.72, 1.6, 24),
      new THREE.MeshStandardMaterial({ color: 0xa855f7, transparent: true, opacity: 0.28, roughness: 0.05 })
    );
    jarGlass.position.y = 1.15;
    jarGroup.add(jarGlass);

    // Metallic Lid
    const jarLid = new THREE.Mesh(
      new THREE.CylinderGeometry(0.78, 0.78, 0.18, 24),
      new THREE.MeshStandardMaterial({ color: 0x334155, metalness: 0.9, roughness: 0.1 })
    );
    jarLid.position.y = 2.0;
    jarGroup.add(jarLid);

    // Cluster of Floating Purple Amethyst Crystals inside
    const crystalsCluster = new THREE.Group();
    const crystalMeshList = [];
    for (let c = 0; c < 5; c++) {
      const crGeo = new THREE.OctahedronGeometry(0.22 - c * 0.03);
      const crMat = new THREE.MeshStandardMaterial({
        color: 0xc084fc,
        emissive: 0x9333ea,
        emissiveIntensity: 0.8,
        roughness: 0.1,
      });
      const crMesh = new THREE.Mesh(crGeo, crMat);
      crMesh.position.set((Math.random() - 0.5) * 0.4, 1.0 + c * 0.18, (Math.random() - 0.5) * 0.4);
      crystalsCluster.add(crMesh);
      crystalMeshList.push(crMesh);
    }
    jarGroup.add(crystalsCluster);

    // Pegboard & Retro MCP Cassettes on North Wall
    const pegboard = new THREE.Mesh(
      new THREE.BoxGeometry(2.4, 1.8, 0.08),
      new THREE.MeshStandardMaterial({ color: 0x111827, roughness: 0.7 })
    );
    pegboard.position.set(-2.5, 1.9, -6.6);
    pegboard.name = "prop_mcp";
    workshopGroup.add(pegboard);

    // Colorful Mini Cassette Tapes on Pegboard
    const cassetteColors = [0x00f0ff, 0xa855f7, 0x10b981, 0xf59e0b];
    for (let i = 0; i < 4; i++) {
      const cas = new THREE.Mesh(
        new THREE.BoxGeometry(0.42, 0.26, 0.06),
        new THREE.MeshStandardMaterial({ color: cassetteColors[i], emissive: cassetteColors[i], emissiveIntensity: 0.3 })
      );
      cas.position.set(-3.2 + i * 0.5, 2.0, -6.52);
      workshopGroup.add(cas);
    }

    // Mini Conveyor + Robotic Arm + Laser Gates + Delivery Chute (Stages 06–08)
    const conveyorGroup = new THREE.Group();
    conveyorGroup.position.set(0.4, 0, -3.2);
    conveyorGroup.name = "prop_conveyor";
    workshopGroup.add(conveyorGroup);

    // Conveyor Bed
    const convBed = new THREE.Mesh(
      new THREE.BoxGeometry(1.0, 0.45, 3.8),
      new THREE.MeshStandardMaterial({ color: 0x1e293b, metalness: 0.7, roughness: 0.3 })
    );
    convBed.position.y = 0.22;
    conveyorGroup.add(convBed);

    // Belt Track
    const convBelt = new THREE.Mesh(
      new THREE.BoxGeometry(0.72, 0.06, 3.75),
      new THREE.MeshStandardMaterial({ color: 0x0284c7, roughness: 0.6 })
    );
    convBelt.position.y = 0.46;
    conveyorGroup.add(convBelt);

    // Moving Cyber Cubes on Belt
    const conveyorCubes = [];
    for (let cb = 0; cb < 3; cb++) {
      const cube = new THREE.Mesh(
        new THREE.BoxGeometry(0.34, 0.34, 0.34),
        new THREE.MeshStandardMaterial({ color: 0x00f0ff, emissive: 0x0284c7, emissiveIntensity: 0.5 })
      );
      cube.position.set(0, 0.65, -1.2 + cb * 1.2);
      conveyorGroup.add(cube);
      conveyorCubes.push(cube);
    }

    // Triple Laser Scanning Arches (L0 green, L1 cyan, L2 orange)
    const laserColors = [0x10b981, 0x00f0ff, 0xf97316];
    for (let l = 0; l < 3; l++) {
      const arch = new THREE.Mesh(
        new THREE.TorusGeometry(0.55, 0.04, 8, 16, Math.PI),
        new THREE.MeshStandardMaterial({ color: laserColors[l], emissive: laserColors[l], emissiveIntensity: 0.9 })
      );
      arch.rotation.y = Math.PI / 2;
      arch.position.set(0, 0.45, -0.8 + l * 0.8);
      conveyorGroup.add(arch);
    }

    // Mini Articulated Robotic Arm
    const robotArmGroup = new THREE.Group();
    robotArmGroup.position.set(-0.75, 0.4, 0);
    const armBase = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.24, 0.2, 12), new THREE.MeshStandardMaterial({ color: 0xf59e0b }));
    const armSegment1 = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.7, 8), new THREE.MeshStandardMaterial({ color: 0x334155 }));
    armSegment1.position.set(0.15, 0.4, 0);
    armSegment1.rotation.z = -0.4;
    const armNozzle = new THREE.Mesh(new THREE.SphereGeometry(0.08, 8, 8), new THREE.MeshBasicMaterial({ color: 0x00f0ff }));
    armNozzle.position.set(0.35, 0.65, 0);
    robotArmGroup.add(armBase, armSegment1, armNozzle);
    conveyorGroup.add(robotArmGroup);

    // Front Delivery Chute (Stage 08 Release)
    const chute = new THREE.Mesh(
      new THREE.BoxGeometry(0.85, 0.1, 0.8),
      new THREE.MeshStandardMaterial({ color: 0x10b981, emissive: 0x059669, emissiveIntensity: 0.4 })
    );
    chute.rotation.x = 0.3;
    chute.position.set(0, 0.3, 2.2);
    chute.name = "prop_chute";
    conveyorGroup.add(chute);

    // 5. ZONE 2: 🏕️ 荧光森系露营休息区 (South-West)
    const campGroup = new THREE.Group();
    scene.add(campGroup);

    // Moss Turf Pad
    const mossPad = new THREE.Mesh(
      new THREE.CylinderGeometry(4.2, 4.5, 0.08, 32),
      new THREE.MeshStandardMaterial({ color: 0x064e3b, roughness: 0.9 })
    );
    mossPad.position.set(-4.2, 0.02, 3.8);
    campGroup.add(mossPad);

    // Glowing Cyan Stream Ribbon
    const streamCurve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(-8.2, 0.05, 1.8),
      new THREE.Vector3(-6.2, 0.05, 2.2),
      new THREE.Vector3(-4.4, 0.05, 2.8),
      new THREE.Vector3(-2.2, 0.05, 1.8),
      new THREE.Vector3(-1.0, 0.05, 2.6),
    ]);
    const streamGeo = new THREE.TubeGeometry(streamCurve, 32, 0.28, 8, false);
    const streamMat = new THREE.MeshBasicMaterial({ color: 0x06b6d4, transparent: true, opacity: 0.8 });
    const streamMesh = new THREE.Mesh(streamGeo, streamMat);
    campGroup.add(streamMesh);

    // 3D Mini Glowing Mushrooms
    for (let m = 0; m < 8; m++) {
      const shroomStem = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.06, 0.25), new THREE.MeshStandardMaterial({ color: 0xffffff }));
      const shroomCap = new THREE.Mesh(
        new THREE.SphereGeometry(0.14, 12, 12, 0, Math.PI * 2, 0, Math.PI / 2),
        new THREE.MeshStandardMaterial({
          color: m % 2 === 0 ? 0xf43f5e : 0x10b981,
          emissive: m % 2 === 0 ? 0xf43f5e : 0x10b981,
          emissiveIntensity: 0.6,
        })
      );
      shroomCap.position.y = 0.14;
      const shroom = new THREE.Group();
      shroom.add(shroomStem, shroomCap);
      shroom.position.set(-6.5 + Math.random() * 4.5, 0.05, 2.5 + Math.random() * 3.0);
      campGroup.add(shroom);
    }

    // White A-Frame Tent
    const tentGroup = new THREE.Group();
    tentGroup.position.set(-6.2, 0, 4.5);
    tentGroup.rotation.y = 0.4;
    const tentOuter = new THREE.Mesh(
      new THREE.ConeGeometry(1.6, 2.0, 3),
      new THREE.MeshStandardMaterial({ color: 0xf8fafc, roughness: 0.6 })
    );
    tentOuter.rotation.y = Math.PI / 6;
    tentOuter.position.y = 1.0;
    tentGroup.add(tentOuter);

    const tentLight = new THREE.PointLight(0xfef08a, 1.2, 3);
    tentLight.position.set(0, 0.6, 0.8);
    tentGroup.add(tentLight);
    campGroup.add(tentGroup);

    // Wooden Log Bench
    const logBench = new THREE.Mesh(
      new THREE.CylinderGeometry(0.24, 0.24, 1.6, 12),
      new THREE.MeshStandardMaterial({ color: 0x78350f, roughness: 0.8 })
    );
    logBench.rotation.z = Math.PI / 2;
    logBench.position.set(-2.8, 0.2, 3.8);
    campGroup.add(logBench);

    // Holographic Campfire
    const campfireGroup = new THREE.Group();
    campfireGroup.position.set(-4.0, 0, 4.0);
    campfireGroup.name = "prop_campfire";
    campGroup.add(campfireGroup);

    // Stones Ring
    for (let r = 0; r < 8; r++) {
      const stone = new THREE.Mesh(new THREE.DodecahedronGeometry(0.14), new THREE.MeshStandardMaterial({ color: 0x475569 }));
      const a = (r / 8) * Math.PI * 2;
      stone.position.set(Math.cos(a) * 0.45, 0.08, Math.sin(a) * 0.45);
      campfireGroup.add(stone);
    }

    // Flame Core
    const flameCore = new THREE.Mesh(
      new THREE.OctahedronGeometry(0.26),
      new THREE.MeshBasicMaterial({ color: 0x22d3ee })
    );
    flameCore.position.y = 0.32;
    campfireGroup.add(flameCore);

    // Rising Campfire Embers Particle System
    const emberCount = 24;
    const emberGeo = new THREE.BufferGeometry();
    const emberPos = new Float32Array(emberCount * 3);
    for (let e = 0; e < emberCount; e++) {
      emberPos[e * 3 + 0] = (Math.random() - 0.5) * 0.4;
      emberPos[e * 3 + 1] = 0.3 + Math.random() * 1.5;
      emberPos[e * 3 + 2] = (Math.random() - 0.5) * 0.4;
    }
    emberGeo.setAttribute("position", new THREE.BufferAttribute(emberPos, 3));
    const emberMat = new THREE.PointsMaterial({ color: 0x38bdf8, size: 0.08, transparent: true, opacity: 0.9 });
    const emberPoints = new THREE.Points(emberGeo, emberMat);
    campfireGroup.add(emberPoints);

    // 6. ZONE 3: 🏋️ 霓虹胶囊健身活力区 (East)
    const gymGroup = new THREE.Group();
    scene.add(gymGroup);

    // Gym Floor Mat
    const gymMat = new THREE.Mesh(
      new THREE.BoxGeometry(6.2, 0.04, 11.2),
      new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.4, metalness: 0.1 })
    );
    gymMat.position.set(5.1, 0.02, 0);
    gymGroup.add(gymMat);

    // Magenta & Mint Track Lines on Gym Mat
    const lineMat = new THREE.MeshBasicMaterial({ color: 0xf43f5e });
    const line1 = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.05, 9.8), lineMat);
    line1.position.set(3.4, 0.03, 0);
    const line2 = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.05, 9.8), new THREE.MeshBasicMaterial({ color: 0x10b981 }));
    line2.position.set(6.8, 0.03, 0);
    gymGroup.add(line1, line2);

    // Translucent Acrylic Treadmill
    const treadmillGroup = new THREE.Group();
    treadmillGroup.position.set(5.8, 0, -2.8);
    treadmillGroup.name = "prop_treadmill";
    gymGroup.add(treadmillGroup);

    const tmBase = new THREE.Mesh(
      new THREE.BoxGeometry(1.2, 0.22, 2.6),
      new THREE.MeshStandardMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.4, roughness: 0.1 })
    );
    tmBase.position.y = 0.14;
    const tmBelt = new THREE.Mesh(
      new THREE.BoxGeometry(0.88, 0.05, 2.4),
      new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.8 })
    );
    tmBelt.position.y = 0.26;
    const tmHandlebars = new THREE.Mesh(
      new THREE.TorusGeometry(0.48, 0.04, 8, 16, Math.PI),
      new THREE.MeshStandardMaterial({ color: 0x00f0ff, metalness: 0.8 })
    );
    tmHandlebars.rotation.x = Math.PI / 2;
    tmHandlebars.position.set(0, 1.0, -1.0);
    treadmillGroup.add(tmBase, tmBelt, tmHandlebars);

    // Neon Dumbbells Rack & Floor Mat
    const dumbbellGroup = new THREE.Group();
    dumbbellGroup.position.set(4.8, 0, 1.8);
    dumbbellGroup.name = "prop_dumbbell";
    gymGroup.add(dumbbellGroup);

    const dbRack = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.7, 0.5), new THREE.MeshStandardMaterial({ color: 0x1e293b }));
    dbRack.position.set(0, 0.35, -0.6);
    dumbbellGroup.add(dbRack);

    // Pink Yoga Mat
    const yogaMat = new THREE.Mesh(new THREE.BoxGeometry(1.4, 0.02, 2.0), new THREE.MeshStandardMaterial({ color: 0xf43f5e, roughness: 0.6 }));
    yogaMat.position.set(-0.2, 0.03, 0.4);
    dumbbellGroup.add(yogaMat);

    // Mini dumbbells on rack
    for (let d = 0; d < 3; d++) {
      const db = new THREE.Mesh(
        new THREE.CylinderGeometry(0.06, 0.06, 0.38),
        new THREE.MeshStandardMaterial({ color: 0x10b981, emissive: 0x10b981, emissiveIntensity: 0.6 })
      );
      db.rotation.z = Math.PI / 2;
      db.position.set(-0.35 + d * 0.35, 0.76, -0.6);
      dumbbellGroup.add(db);
    }

    // Glowing Cyber Soda Vending Machine
    const vendingGroup = new THREE.Group();
    vendingGroup.position.set(6.2, 0, -5.2);
    vendingGroup.name = "prop_vending";
    gymGroup.add(vendingGroup);

    const vendBody = new THREE.Mesh(
      new THREE.BoxGeometry(1.5, 2.6, 1.1),
      new THREE.MeshStandardMaterial({ color: 0x0f172a, metalness: 0.8, roughness: 0.2 })
    );
    vendBody.position.y = 1.3;
    const vendScreen = new THREE.Mesh(
      new THREE.PlaneGeometry(1.1, 1.2),
      new THREE.MeshBasicMaterial({ color: 0x00f0ff, transparent: true, opacity: 0.6 })
    );
    vendScreen.position.set(0, 1.5, 0.56);
    const vendMarquee = new THREE.Mesh(
      new THREE.BoxGeometry(1.3, 0.3, 0.1),
      new THREE.MeshStandardMaterial({ color: 0xf43f5e, emissive: 0xf43f5e, emissiveIntensity: 0.8 })
    );
    vendMarquee.position.set(0, 2.35, 0.52);
    vendingGroup.add(vendBody, vendScreen, vendMarquee);

    // 7. HAND-CRAFTED POP-MART CHIBI 3D CHARACTER (Neko-01)
    const nekoRoot = new THREE.Group();
    nekoRoot.position.set(-2.8, 0, 3.8); // Starts by the campfire
    scene.add(nekoRoot);

    // Floor Pulsing Glow Ring & Shadow Disc
    const nekoShadow = new THREE.Mesh(
      new THREE.CircleGeometry(0.36, 24),
      new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.4 })
    );
    nekoShadow.rotation.x = -Math.PI / 2;
    nekoShadow.position.y = 0.01;
    nekoRoot.add(nekoShadow);

    const nekoAura = new THREE.Mesh(
      new THREE.RingGeometry(0.42, 0.5, 32),
      new THREE.MeshBasicMaterial({ color: 0x00ff9d, transparent: true, opacity: 0.75, side: THREE.DoubleSide })
    );
    nekoAura.rotation.x = -Math.PI / 2;
    nekoAura.position.y = 0.02;
    nekoRoot.add(nekoAura);

    // Head Group (y: 1.15)
    const headGroup = new THREE.Group();
    headGroup.position.y = 1.15;
    nekoRoot.add(headGroup);

    // Porcelain Head Base
    const headMat = new THREE.MeshStandardMaterial({ color: 0xffede4, roughness: 0.5 });
    const headMesh = new THREE.Mesh(new THREE.SphereGeometry(0.44, 32, 32), headMat);
    headMesh.scale.set(1.0, 0.95, 0.92);
    headGroup.add(headMesh);

    // Silver-Grey Bob Hair with Blunt Straight Bangs (齐刘海)
    const hairMat = new THREE.MeshStandardMaterial({ color: 0xd1d5db, roughness: 0.4 });
    const hairCap = new THREE.Mesh(
      new THREE.SphereGeometry(0.46, 24, 24, 0, Math.PI * 2, 0, Math.PI * 0.55),
      hairMat
    );
    hairCap.position.y = 0.05;
    headGroup.add(hairCap);

    // Straight Bangs Plane
    const bangs = new THREE.Mesh(
      new THREE.BoxGeometry(0.48, 0.22, 0.06),
      hairMat
    );
    bangs.position.set(0, 0.16, 0.4);
    headGroup.add(bangs);

    // Rosy Pink Cheek Blushes
    const blushMat = new THREE.MeshBasicMaterial({ color: 0xf472b6, transparent: true, opacity: 0.55 });
    const blushL = new THREE.Mesh(new THREE.CircleGeometry(0.08, 16), blushMat);
    blushL.position.set(-0.24, -0.04, 0.42);
    const blushR = new THREE.Mesh(new THREE.CircleGeometry(0.08, 16), blushMat);
    blushR.position.set(0.24, -0.04, 0.42);
    headGroup.add(blushL, blushR);

    // Glossy Anime Eyes with Catchlights & Blink
    const eyeMat = new THREE.MeshBasicMaterial({ color: 0x0f766e });
    const eyeL = new THREE.Mesh(new THREE.SphereGeometry(0.075, 16, 16), eyeMat);
    eyeL.position.set(-0.16, 0.05, 0.4);
    const eyeR = new THREE.Mesh(new THREE.SphereGeometry(0.075, 16, 16), eyeMat);
    eyeR.position.set(0.16, 0.05, 0.4);

    const catchL = new THREE.Mesh(new THREE.SphereGeometry(0.024, 8, 8), new THREE.MeshBasicMaterial({ color: 0xffffff }));
    catchL.position.set(-0.14, 0.07, 0.46);
    const catchR = new THREE.Mesh(new THREE.SphereGeometry(0.024, 8, 8), new THREE.MeshBasicMaterial({ color: 0xffffff }));
    catchR.position.set(0.18, 0.07, 0.46);
    headGroup.add(eyeL, eyeR, catchL, catchR);

    // Oversized White Hoodie Hood Wrapping Head
    const hoodMat = new THREE.MeshStandardMaterial({ color: 0xf8fafc, roughness: 0.7 });
    const hoodOuter = new THREE.Mesh(
      new THREE.SphereGeometry(0.5, 24, 24, 0, Math.PI * 2, Math.PI * 0.25, Math.PI * 0.75),
      hoodMat
    );
    hoodOuter.rotation.x = Math.PI / 2;
    hoodOuter.position.set(0, 0, -0.06);
    headGroup.add(hoodOuter);

    // Two Chubby Cat Ears with Neon High-Emissive Inner Ears
    const earOuterMat = new THREE.MeshStandardMaterial({ color: 0xf8fafc, roughness: 0.5 });
    const earInnerMat = new THREE.MeshStandardMaterial({ color: 0x00ff9d, emissive: 0x00ff9d, emissiveIntensity: 0.95 });

    const createEar = (isLeft) => {
      const earGroup = new THREE.Group();
      const outer = new THREE.Mesh(new THREE.ConeGeometry(0.16, 0.35, 12), earOuterMat);
      const inner = new THREE.Mesh(new THREE.ConeGeometry(0.1, 0.24, 12), earInnerMat);
      inner.position.z = 0.05;
      earGroup.add(outer, inner);
      earGroup.position.set(isLeft ? -0.28 : 0.28, 0.42, 0.08);
      earGroup.rotation.z = isLeft ? 0.3 : -0.3;
      return { earGroup, inner };
    };

    const earLObj = createEar(true);
    const earRObj = createEar(false);
    headGroup.add(earLObj.earGroup, earRObj.earGroup);

    // Subtle Head PointLight illuminating face & reflecting ear state
    const nekoFaceLight = new THREE.PointLight(0x00ff9d, 0.65, 2.5);
    nekoFaceLight.position.set(0, 0.2, 0.7);
    headGroup.add(nekoFaceLight);

    // Body & Chubby Hoodie Torso (y: 0.65)
    const bodyGroup = new THREE.Group();
    bodyGroup.position.y = 0.65;
    nekoRoot.add(bodyGroup);

    const torsoMesh = new THREE.Mesh(new THREE.CylinderGeometry(0.24, 0.28, 0.5, 16), hoodMat);
    torsoMesh.position.y = -0.05;
    bodyGroup.add(torsoMesh);

    // Kangaroo Pocket
    const pocket = new THREE.Mesh(new THREE.BoxGeometry(0.32, 0.16, 0.08), hoodMat);
    pocket.position.set(0, -0.15, 0.24);
    bodyGroup.add(pocket);

    // Articulated Arms
    const armL = new THREE.Group();
    armL.position.set(-0.34, 0.1, 0);
    const armR = new THREE.Group();
    armR.position.set(0.34, 0.1, 0);

    const armMeshL = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.32), hoodMat);
    armMeshL.position.y = -0.16;
    const armMeshR = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.32), hoodMat);
    armMeshR.position.y = -0.16;

    const cuffMat = new THREE.MeshStandardMaterial({ color: 0x00f0ff, emissive: 0x00f0ff, emissiveIntensity: 0.6 });
    const cuffL = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.09, 0.06), cuffMat);
    cuffL.position.y = -0.3;
    const cuffR = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.09, 0.06), cuffMat);
    cuffR.position.y = -0.3;

    const handL = new THREE.Mesh(new THREE.SphereGeometry(0.08, 12, 12), headMat);
    handL.position.y = -0.36;
    const handR = new THREE.Mesh(new THREE.SphereGeometry(0.08, 12, 12), headMat);
    handR.position.y = -0.36;

    // Attachable Props
    // Prop 1: Neon Dumbbells in hands
    const dbPropL = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.24), new THREE.MeshStandardMaterial({ color: 0x10b981, emissive: 0x10b981 }));
    dbPropL.rotation.x = Math.PI / 2;
    dbPropL.position.set(0, -0.36, 0);
    dbPropL.visible = false;

    const dbPropR = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.24), new THREE.MeshStandardMaterial({ color: 0x10b981, emissive: 0x10b981 }));
    dbPropR.rotation.x = Math.PI / 2;
    dbPropR.position.set(0, -0.36, 0);
    dbPropR.visible = false;

    // Prop 2: Held Purple Crystal
    const heldCrystal = new THREE.Mesh(
      new THREE.OctahedronGeometry(0.18),
      new THREE.MeshStandardMaterial({ color: 0xc084fc, emissive: 0xa855f7, emissiveIntensity: 0.9 })
    );
    heldCrystal.position.set(0, -0.22, 0.35);
    heldCrystal.visible = false;

    armL.add(armMeshL, cuffL, handL, dbPropL);
    armR.add(armMeshR, cuffR, handR, dbPropR);
    bodyGroup.add(armL, armR, heldCrystal);

    // Articulated Legs & Translucent Glowing Jelly Snow Boots
    const legL = new THREE.Group();
    legL.position.set(-0.14, 0.36, 0);
    const legR = new THREE.Group();
    legR.position.set(0.14, 0.36, 0);

    const legMeshL = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.22), hoodMat);
    legMeshL.position.y = -0.1;
    const legMeshR = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.22), hoodMat);
    legMeshR.position.y = -0.1;

    const bootMat = new THREE.MeshStandardMaterial({ color: 0x00f0ff, transparent: true, opacity: 0.75, roughness: 0.1 });
    const bootL = new THREE.Mesh(new THREE.BoxGeometry(0.18, 0.16, 0.28), bootMat);
    bootL.position.set(0, -0.26, 0.04);
    const bootR = new THREE.Mesh(new THREE.BoxGeometry(0.18, 0.16, 0.28), bootMat);
    bootR.position.set(0, -0.26, 0.04);

    legL.add(legMeshL, bootL);
    legR.add(legMeshR, bootR);
    nekoRoot.add(legL, legR);

    // Click Detection Mesh on Neko for Raycaster
    const nekoHitbox = new THREE.Mesh(
      new THREE.CylinderGeometry(0.55, 0.55, 1.8),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0 })
    );
    nekoHitbox.position.y = 0.9;
    nekoHitbox.name = "character_neko";
    nekoRoot.add(nekoHitbox);

    // Floor Target Ring for Click-to-Walk
    const targetRing = new THREE.Mesh(
      new THREE.RingGeometry(0.3, 0.38, 32),
      new THREE.MeshBasicMaterial({ color: 0x00f0ff, side: THREE.DoubleSide })
    );
    targetRing.rotation.x = -Math.PI / 2;
    targetRing.position.set(0, 0.02, 0);
    targetRing.visible = false;
    scene.add(targetRing);

    // Interactive Raycaster Setup
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();

    const handlePointerDown = (event) => {
      const rect = renderer.domElement.getBoundingClientRect();
      mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(scene.children, true);

      if (intersects.length > 0) {
        // Check for click on Neko-01
        const clickedNeko = intersects.find((hit) => hit.object.name === "character_neko" || hit.object === nekoHitbox);
        if (clickedNeko) {
          setShowNekoPanel((prev) => !prev);
          setBubbleText("✨ 喵！指挥官，我在！有什么战术指令要交给我吗？");
          runtimeRef.current.nekoJumpSpin = 1.0;
          return;
        }

        // Check for click on Interactive Props
        for (const hit of intersects) {
          let obj = hit.object;
          while (obj && !obj.name && obj.parent) obj = obj.parent;
          const name = obj?.name;

          if (name === "prop_notes") {
            openStageDrawer("wbs");
            return;
          }
          if (name === "prop_table") {
            openStageDrawer("build");
            return;
          }
          if (name === "prop_stamp") {
            onOpenApproval();
            return;
          }
          if (name === "prop_jar") {
            openStageDrawer("retro");
            return;
          }
          if (name === "prop_mcp") {
            onOpenExchange();
            return;
          }
          if (name === "prop_conveyor" || name === "prop_chute") {
            openStageDrawer(name === "prop_chute" ? "release" : "test");
            return;
          }
        }

        // Click on Floor: 《梦幻西游》-style Click-to-Walk!
        const floorHit = intersects.find((hit) => hit.point.y >= -0.05 && hit.point.y <= 0.1);
        if (floorHit) {
          const pt = floorHit.point;
          // Spawn glowing cyan ripple on ground
          targetRing.position.set(pt.x, 0.02, pt.z);
          targetRing.visible = true;
          setClickRipple({ x: event.clientX - rect.left, y: event.clientY - rect.top });
          setTimeout(() => setClickRipple(null), 800);

          // Route Neko to clicked coordinates!
          const nearest = getNearestNode(pt.x, pt.z);
          runtimeRef.current.manualInterruptUntil = Date.now() + 8000;
          runtimeRef.current.currentAction = "walk_to_floor";
          runtimeRef.current.customFloorTarget = { x: pt.x, z: pt.z };
          const startNode = getNearestNode(runtimeRef.current.nekoPos.x, runtimeRef.current.nekoPos.z);
          runtimeRef.current.pathQueue = [...findPath(startNode, nearest), "custom_floor"];
          runtimeRef.current.isWalking = true;
          setBubbleText("🐾 收到！这就走过去看看！");
        }
      }
    };

    renderer.domElement.addEventListener("pointerdown", handlePointerDown);

    // Mouse drag for camera orbit/pan & wheel for zoom
    let isDragging = false;
    let prevMouse = { x: 0, y: 0 };
    let cameraTarget = new THREE.Vector3(0, 0, 0);

    const handleMouseDown = (e) => {
      isDragging = true;
      prevMouse = { x: e.clientX, y: e.clientY };
    };

    const handleMouseMove = (e) => {
      if (!isDragging) return;
      const dx = e.clientX - prevMouse.x;
      const dy = e.clientY - prevMouse.y;
      prevMouse = { x: e.clientX, y: e.clientY };

      // Orbit camera around cameraTarget
      const angle = dx * 0.005;
      const offset = camera.position.clone().sub(cameraTarget);
      offset.applyAxisAngle(new THREE.Vector3(0, 1, 0), -angle);
      camera.position.copy(cameraTarget).add(offset);
      camera.lookAt(cameraTarget);
    };

    const handleMouseUp = () => {
      isDragging = false;
    };

    const handleWheel = (e) => {
      e.preventDefault();
      const zoomFactor = e.deltaY > 0 ? 1.08 : 0.92;
      camera.left *= zoomFactor;
      camera.right *= zoomFactor;
      camera.top *= zoomFactor;
      camera.bottom *= zoomFactor;
      camera.updateProjectionMatrix();
    };

    renderer.domElement.addEventListener("mousedown", handleMouseDown);
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    renderer.domElement.addEventListener("wheel", handleWheel, { passive: false });

    // Handle Window Resize
    const handleResize = () => {
      if (!container) return;
      const w = container.clientWidth || window.innerWidth;
      const h = container.clientHeight || (window.innerHeight - 56);
      const asp = w / h;
      camera.left = (-frustumSize * asp) / 2;
      camera.right = (frustumSize * asp) / 2;
      camera.top = frustumSize / 2;
      camera.bottom = -frustumSize / 2;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener("resize", handleResize);

    // 8. 60FPS ANIMATION LOOP
    let animationFrameId;
    let clock = new THREE.Clock();
    let nextIdleDecisionTime = Date.now() + 6000;
    let nextBlinkTime = Date.now() + 3000;

    const animate = () => {
      animationFrameId = requestAnimationFrame(animate);

      const delta = clock.getDelta();
      const time = clock.getElapsedTime();
      const rt = runtimeRef.current;

      // 8.1 Autonomous Idle Decision Loop
      if (Date.now() > nextIdleDecisionTime && Date.now() > rt.manualInterruptUntil) {
        const hasActiveStage = Object.values(rt.stagesState).some((s) => s === "active");
        if (!hasActiveStage && !rt.budgetAlert80 && !rt.approvalModal) {
          const act = IDLE_ACTIVITIES[Math.floor(Math.random() * IDLE_ACTIVITIES.length)];
          orderNekoTo(act.key, act.bubble, act.action);
        }
        nextIdleDecisionTime = Date.now() + 8000 + Math.random() * 4000;
      }

      // 8.2 Blink Animation
      if (Date.now() > nextBlinkTime) {
        eyeL.scale.y = 0.1;
        eyeR.scale.y = 0.1;
        setTimeout(() => {
          eyeL.scale.y = 1.0;
          eyeR.scale.y = 1.0;
        }, 150);
        nextBlinkTime = Date.now() + 3000 + Math.random() * 4000;
      }

      // 8.3 Dynamic Ear & Light Colors reflecting System State
      let earColor = 0x00ff9d; // Normal idle mint
      if (rt.budgetAlert80 || rt.approvalModal) {
        earColor = 0xff0055; // Coral red alert
      } else if (Object.values(rt.stagesState).some((s) => s === "active")) {
        earColor = 0x00f0ff; // Working cyan
      } else if (rt.crystalInHand) {
        earColor = 0xa855f7; // Amethyst purple
      }
      earInnerMat.color.setHex(earColor);
      earInnerMat.emissive.setHex(earColor);
      nekoFaceLight.color.setHex(earColor);
      nekoAura.material.color.setHex(earColor);

      // 8.4 Autonomous Multi-Waypoint Walking Physics
      if (rt.isWalking && rt.pathQueue.length > 0) {
        const nextNodeKey = rt.pathQueue[0];
        let targetX = 0, targetZ = 0;

        if (nextNodeKey === "custom_floor" && rt.customFloorTarget) {
          targetX = rt.customFloorTarget.x;
          targetZ = rt.customFloorTarget.z;
        } else {
          const node = NAV_NODES[nextNodeKey];
          if (node) {
            targetX = node.x;
            targetZ = node.z;
          }
        }

        const dx = targetX - rt.nekoPos.x;
        const dz = targetZ - rt.nekoPos.z;
        const dist = Math.hypot(dx, dz);

        if (dist > 0.1) {
          // Rotate toward heading smoothly
          const targetAngle = Math.atan2(dx, dz);
          nekoRoot.rotation.y = THREE.MathUtils.lerp(nekoRoot.rotation.y, targetAngle, 0.15);

          // Step forward
          const step = Math.min(rt.walkSpeed * delta, dist);
          rt.nekoPos.x += (dx / dist) * step;
          rt.nekoPos.z += (dz / dist) * step;
          nekoRoot.position.set(rt.nekoPos.x, 0, rt.nekoPos.z);

          // Brisk Walking Gait: leg & arm swing + head bob + ear bounce
          const walkPhase = time * 12;
          legL.rotation.x = Math.sin(walkPhase) * 0.55;
          legR.rotation.x = -Math.sin(walkPhase) * 0.55;
          armL.rotation.x = -Math.sin(walkPhase) * 0.45;
          armR.rotation.x = Math.sin(walkPhase) * 0.45;
          headGroup.position.y = 1.15 + Math.abs(Math.sin(walkPhase)) * 0.05;
          earLObj.earGroup.rotation.x = Math.sin(walkPhase) * 0.12;
          earRObj.earGroup.rotation.x = -Math.sin(walkPhase) * 0.12;

          targetRing.visible = true;
        } else {
          // Reached waypoint
          rt.pathQueue.shift();
          if (rt.pathQueue.length === 0) {
            rt.isWalking = false;
            targetRing.visible = false;

            // Face station direction if specified
            const finalNode = NAV_NODES[rt.targetStation];
            if (finalNode?.face) {
              const fdx = finalNode.face.x - rt.nekoPos.x;
              const fdz = finalNode.face.z - rt.nekoPos.z;
              nekoRoot.rotation.y = Math.atan2(fdx, fdz);
            }
          }
        }
      } else {
        // 8.5 Station Specific Idle Animations (When standing still)
        legL.rotation.x = 0;
        legR.rotation.x = 0;
        headGroup.position.y = 1.15;

        // Reset props visibility
        dbPropL.visible = rt.currentAction === "curls";
        dbPropR.visible = rt.currentAction === "curls";
        heldCrystal.visible = rt.crystalInHand;

        if (rt.currentAction === "jog") {
          // Jogging on treadmill
          const jogPhase = time * 14;
          legL.rotation.x = Math.sin(jogPhase) * 0.6;
          legR.rotation.x = -Math.sin(jogPhase) * 0.6;
          armL.rotation.x = -Math.sin(jogPhase) * 0.5;
          armR.rotation.x = Math.sin(jogPhase) * 0.5;
          headGroup.position.y = 1.15 + Math.abs(Math.sin(jogPhase)) * 0.08;
        } else if (rt.currentAction === "curls") {
          // Dumbbell arm curls
          armL.rotation.x = -0.3 + Math.sin(time * 3) * 0.8;
          armR.rotation.x = -0.3 + Math.cos(time * 3) * 0.8;
        } else if (rt.currentAction === "typing") {
          // Rapid holographic coding
          armL.rotation.x = -0.9 + Math.sin(time * 16) * 0.2;
          armR.rotation.x = -0.9 + Math.cos(time * 16) * 0.2;
          screensGroup.rotation.y = 0.3 + Math.sin(time * 2) * 0.05;
        } else if (rt.currentAction === "sit_fire") {
          // Sitting on log swinging boots
          legL.rotation.x = 0.6 + Math.sin(time * 3) * 0.25;
          legR.rotation.x = 0.6 + Math.cos(time * 3) * 0.25;
          armL.rotation.x = -0.2;
          armR.rotation.x = -0.2;
        } else if (rt.currentAction === "stick_notes") {
          // Moving arm sticking notes on board
          armL.rotation.x = -1.2 + Math.sin(time * 4) * 0.3;
          armR.rotation.x = -0.3;
        } else {
          // Gentle breathing idle
          bodyGroup.position.y = 0.65 + Math.sin(time * 2.5) * 0.015;
          headGroup.position.y = 1.15 + Math.sin(time * 2.5) * 0.02;
          armL.rotation.x = Math.sin(time * 1.5) * 0.08;
          armR.rotation.x = -Math.sin(time * 1.5) * 0.08;
        }

        // Jump spin if clicked
        if (rt.nekoJumpSpin && rt.nekoJumpSpin > 0) {
          nekoRoot.rotation.y += delta * 15;
          nekoRoot.position.y = Math.sin(rt.nekoJumpSpin * Math.PI) * 0.5;
          rt.nekoJumpSpin -= delta * 2;
          if (rt.nekoJumpSpin <= 0) nekoRoot.position.y = 0;
        }
      }

      // 8.6 Environmental Dynamic Animations
      // Flickering holographic campfire light
      campLight.intensity = 1.8 + Math.sin(time * 10) * 0.3 + Math.cos(time * 17) * 0.2;
      flameCore.rotation.y += delta * 2;
      flameCore.scale.setScalar(0.9 + Math.sin(time * 8) * 0.15);

      // Rising campfire embers
      const positions = emberGeo.attributes.position.array;
      for (let i = 0; i < emberCount; i++) {
        positions[i * 3 + 1] += delta * 0.8;
        if (positions[i * 3 + 1] > 2.0) {
          positions[i * 3 + 1] = 0.3;
          positions[i * 3 + 0] = (Math.random() - 0.5) * 0.35;
          positions[i * 3 + 2] = (Math.random() - 0.5) * 0.35;
        }
      }
      emberGeo.attributes.position.needsUpdate = true;

      // Rotating amethyst crystals in jar
      crystalsCluster.rotation.y += delta * 0.6;
      crystalMeshList.forEach((cr, idx) => {
        cr.position.y += Math.sin(time * 2 + idx) * 0.001;
      });

      // Conveyor moving cubes
      conveyorCubes.forEach((cube) => {
        cube.position.z += delta * 0.6;
        if (cube.position.z > 1.8) cube.position.z = -1.6;
      });

      // Robotic Arm pivoting if building
      if (rt.stagesState.build === "active") {
        robotArmGroup.rotation.y = Math.sin(time * 4) * 0.6;
      }

      // 8.7 Camera Preset Interpolation
      let targetCamPos = camera.position.clone();
      let targetLook = cameraTarget.clone();

      if (rt.followNeko) {
        targetLook.set(rt.nekoPos.x, 0.8, rt.nekoPos.z);
        targetCamPos.set(rt.nekoPos.x + 16, 18, rt.nekoPos.z + 16);
      } else if (rt.cameraPreset === "workshop") {
        targetLook.set(-4.2, 1.2, -2.5);
        targetCamPos.set(10, 16, 10);
      } else if (rt.cameraPreset === "camp") {
        targetLook.set(-4.0, 0.8, 3.8);
        targetCamPos.set(10, 16, 18);
      } else if (rt.cameraPreset === "gym") {
        targetLook.set(5.2, 1.0, 0);
        targetCamPos.set(18, 16, 12);
      } else {
        // Overview
        targetLook.set(0, 0.5, 0);
        targetCamPos.set(20, 22, 20);
      }

      cameraTarget.lerp(targetLook, 0.05);
      camera.position.lerp(targetCamPos, 0.05);
      camera.lookAt(cameraTarget);

      // 8.8 Project Neko-01 3D World Position to 2D Screen for Thought Bubble
      const headWorldPos = new THREE.Vector3(rt.nekoPos.x, 2.2, rt.nekoPos.z);
      headWorldPos.project(camera);

      const hw = width / 2;
      const hh = height / 2;
      const sx = headWorldPos.x * hw + hw;
      const sy = -headWorldPos.y * hh + hh;

      setBubbleScreenPos({
        x: sx,
        y: sy,
        visible: headWorldPos.z < 1.0,
      });

      renderer.render(scene, camera);
    };

    animationFrameId = requestAnimationFrame(animate);

    // Cleanup on unmount
    return () => {
      cancelAnimationFrame(animationFrameId);
      window.removeEventListener("resize", handleResize);
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
      renderer.domElement.removeEventListener("pointerdown", handlePointerDown);
      renderer.domElement.removeEventListener("mousedown", handleMouseDown);
      renderer.domElement.removeEventListener("wheel", handleWheel);
      if (renderer.domElement.parentNode) {
        renderer.domElement.parentNode.removeChild(renderer.domElement);
      }
      renderer.dispose();
    };
  }, [openStageDrawer, onOpenExchange, onOpenApproval]);

  return (
    <div
      ref={mountRef}
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        overflow: "hidden",
        background: "#070b14",
        userSelect: "none",
      }}
    >
      {/* Click-on-floor expanding ripple animation */}
      {clickRipple && (
        <div
          style={{
            position: "absolute",
            left: clickRipple.x - 24,
            top: clickRipple.y - 24,
            width: 48,
            height: 48,
            borderRadius: "50%",
            border: "2px solid #00f0ff",
            pointerEvents: "none",
            animation: "pulseRing 0.6s ease-out forwards",
            zIndex: 10,
          }}
        />
      )}

      {/* Floating 2D Projected Thought Bubble above Neko-01 */}
      {bubbleScreenPos.visible && (
        <div
          style={{
            position: "absolute",
            left: bubbleScreenPos.x,
            top: bubbleScreenPos.y - 12,
            transform: "translate(-50%, -100%)",
            background: "rgba(9, 14, 28, 0.92)",
            border: "1px solid rgba(0, 240, 255, 0.45)",
            borderRadius: "12px",
            padding: "8px 14px",
            color: "#f8fafc",
            fontSize: "12px",
            fontWeight: "500",
            whiteSpace: "nowrap",
            boxShadow: "0 8px 25px rgba(0,0,0,0.6), 0 0 15px rgba(0, 240, 255, 0.2)",
            backdropFilter: "blur(8px)",
            pointerEvents: "none",
            transition: "all 0.15s ease",
            zIndex: 20,
          }}
        >
          <span>{bubbleText}</span>
          <div
            style={{
              position: "absolute",
              bottom: "-6px",
              left: "50%",
              transform: "translateX(-50%)",
              width: 0,
              height: 0,
              borderLeft: "6px solid transparent",
              borderRight: "6px solid transparent",
              borderTop: "6px solid rgba(9, 14, 28, 0.92)",
            }}
          />
        </div>
      )}

      {/* Interactive Neko-01 Action Card (Opens on clicking Neko) */}
      {showNekoPanel && (
        <div
          style={{
            position: "absolute",
            left: Math.min(Math.max(bubbleScreenPos.x, 180), window.innerWidth - 200),
            top: Math.max(bubbleScreenPos.y - 140, 20),
            transform: "translate(-50%, -100%)",
            width: "320px",
            background: "rgba(9, 14, 28, 0.96)",
            border: "1px solid rgba(0, 240, 255, 0.5)",
            borderRadius: "14px",
            padding: "14px",
            boxShadow: "0 12px 35px rgba(0,0,0,0.8), 0 0 20px rgba(0, 240, 255, 0.3)",
            backdropFilter: "blur(12px)",
            zIndex: 40,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "6px", color: "#00f0ff", fontWeight: "bold", fontSize: "13px" }}>
              <span>🐾</span>
              <span>Neko-01 · 首席赛博助理</span>
            </div>
            <button
              style={{ background: "none", border: "none", color: "#94a3b8", cursor: "pointer", fontSize: "16px" }}
              onClick={() => setShowNekoPanel(false)}
            >
              ×
            </button>
          </div>
          <div style={{ fontSize: "12px", color: "#e2e8f0", lineHeight: "1.5", marginBottom: "12px" }}>
            喵~ 报告主公！我正在虚拟工作室中随时待命！派发指令我就会自主走到对应工位开始生产构建！
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
            <button
              className="cyber-btn cyan"
              style={{ fontSize: "11px", padding: "4px 8px" }}
              onClick={() => {
                onDirectiveFocus();
                setShowNekoPanel(false);
              }}
            >
              ✨ 派发战术指令
            </button>
            <button
              className="cyber-btn"
              style={{ fontSize: "11px", padding: "4px 8px" }}
              onClick={() => {
                orderNekoTo("camp_fire", "🔥 去露营篝火烤烤火~ 晃晃脚丫！", "sit_fire");
                setShowNekoPanel(false);
              }}
            >
              🏕️ 去露营烤火
            </button>
            <button
              className="cyber-btn"
              style={{ fontSize: "11px", padding: "4px 8px" }}
              onClick={() => {
                orderNekoTo("gym_treadmill", "🏃‍♀️ 去跑步机做个有氧热身！", "jog");
                setShowNekoPanel(false);
              }}
            >
              🏃‍♀️ 去跑步机
            </button>
            <button
              className="cyber-btn"
              style={{ fontSize: "11px", padding: "4px 8px" }}
              onClick={() => {
                orderNekoTo("gym_dumbbell", "💪 去粉色垫子举举哑铃！", "curls");
                setShowNekoPanel(false);
              }}
            >
              💪 去举铁
            </button>
            <button
              className="cyber-btn"
              style={{ fontSize: "11px", padding: "4px 8px" }}
              onClick={() => {
                orderNekoTo("work_table", "💻 回到弧形全息台检查算力！", "typing");
                setShowNekoPanel(false);
              }}
            >
              🛠️ 去工作台
            </button>
            <button
              className="cyber-btn purple"
              style={{ fontSize: "11px", padding: "4px 8px" }}
              onClick={() => {
                orderNekoTo("work_jar", "💎 投喂发光紫晶石！经验 +1！", "inspect_jar");
                setShowNekoPanel(false);
              }}
            >
              💎 投喂紫晶石
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
