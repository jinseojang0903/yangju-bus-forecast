// 지도 라이브러리와 무관한 그리기 입력. 화면(features)은 이 타입만 알고,
// Leaflet 같은 라이브러리 이름은 components/map 밖으로 나가지 않는다(카카오맵 등으로 바꿀 때 이 폴더만 고친다).

export interface LatLng {
  lat: number;
  lng: number;
}

export interface RouteMapStop {
  key: string;
  position: LatLng;
  name: string;
  /** 내 정류장. 크게 그리고 이름을 늘 보인다 */
  isTarget: boolean;
}

/** 잔여석 구분. 색만으로 구분하지 않도록 label 과 함께 쓴다 */
export type VehicleSeatTone = "available" | "noSeat" | "unknown";

export interface RouteMapVehicle {
  key: string;
  position: LatLng;
  /** 마커에 보이는 짧은 글자. 예: "3석", "0석", "정보 없음" */
  label: string;
  tone: VehicleSeatTone;
  /** 눌렀을 때 보이는 설명(상태·번호판 등) */
  description: string;
  /** 귀로 구간 등 내 정류장과 상관없는 차는 흐리게 그린다 */
  isDimmed: boolean;
}

export interface RouteMapProps {
  /** 지도 영역의 접근성 이름. 예: "G1300 노선 지도" */
  label: string;
  /** 잠실행 구간 정류장을 순서대로 이은 선(진하게) */
  outboundPath: LatLng[];
  /** 회차 뒤 귀로 구간(흐리게) */
  returnPath: LatLng[];
  /** 잠실행 구간 정류장 점 */
  stops: RouteMapStop[];
  vehicles: RouteMapVehicle[];
  /** 처음 화면과 '다시 맞추기' 때 모두 보이게 맞출 지점들 */
  focus: LatLng[];
  /** 값이 바뀔 때마다 focus 로 다시 맞춘다(처음 그릴 때는 자동) */
  focusRequestId: number;
}
