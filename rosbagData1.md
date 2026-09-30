   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 144.9/144.9 kB 3.1 MB/s eta 0:00:00
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 1.4/1.4 MB 29.0 MB/s eta 0:00:00
HKairport_GNSS03.bag: 397.0 s
/dji_osdk_ros/acceleration_ground_fused       geometry_msgs/msg/Vector3Stamped                 39702 msgs   100.0 Hz
/dji_osdk_ros/angular_velocity_fused          geometry_msgs/msg/Vector3Stamped                 39702 msgs   100.0 Hz
/dji_osdk_ros/attitude                        geometry_msgs/msg/QuaternionStamped              39702 msgs   100.0 Hz
/dji_osdk_ros/battery_state                   sensor_msgs/msg/BatteryState                      1985 msgs     5.0 Hz
/dji_osdk_ros/display_mode                    std_msgs/msg/UInt8                               19851 msgs    50.0 Hz
/dji_osdk_ros/flight_anomaly                  dji_osdk_ros/msg/FlightAnomaly                   19851 msgs    50.0 Hz
/dji_osdk_ros/flight_status                   std_msgs/msg/UInt8                               19851 msgs    50.0 Hz
/dji_osdk_ros/gimbal_angle                    geometry_msgs/msg/Vector3Stamped                 19851 msgs    50.0 Hz
/dji_osdk_ros/gps_health                      std_msgs/msg/UInt8                               19851 msgs    50.0 Hz
/dji_osdk_ros/gps_position                    sensor_msgs/msg/NavSatFix                        19851 msgs    50.0 Hz
/dji_osdk_ros/gps_velocity                    geometry_msgs/msg/Vector3Stamped                  1985 msgs     5.0 Hz
/dji_osdk_ros/gps_velocity_cov                geometry_msgs/msg/Vector3Stamped                  1985 msgs     5.0 Hz
/dji_osdk_ros/height_above_takeoff            std_msgs/msg/Float32                             19851 msgs    50.0 Hz
/dji_osdk_ros/imu                             sensor_msgs/msg/Imu                             158804 msgs   400.0 Hz
/dji_osdk_ros/local_position                  geometry_msgs/msg/PointStamped                   19851 msgs    50.0 Hz
/dji_osdk_ros/rc                              sensor_msgs/msg/Joy                              19851 msgs    50.0 Hz
/dji_osdk_ros/rc_connection_status            std_msgs/msg/UInt8                               19851 msgs    50.0 Hz
/dji_osdk_ros/rtk_connection_status           std_msgs/msg/UInt8                                1985 msgs     5.0 Hz
/dji_osdk_ros/rtk_info_position               std_msgs/msg/UInt8                                1985 msgs     5.0 Hz
/dji_osdk_ros/rtk_info_yaw                    std_msgs/msg/UInt8                                1985 msgs     5.0 Hz
/dji_osdk_ros/rtk_position                    sensor_msgs/msg/NavSatFix                         1985 msgs     5.0 Hz
/dji_osdk_ros/rtk_velocity                    geometry_msgs/msg/Vector3Stamped                  1985 msgs     5.0 Hz
/dji_osdk_ros/rtk_yaw                         std_msgs/msg/Int16                                1985 msgs     5.0 Hz
/dji_osdk_ros/time_sync_fc_time_utc           dji_osdk_ros/msg/FCTimeInUTC                       397 msgs     1.0 Hz
/dji_osdk_ros/time_sync_gps_utc               dji_osdk_ros/msg/GPSUTC                            397 msgs     1.0 Hz
/dji_osdk_ros/time_sync_nmea_msg              nmea_msgs/msg/Sentence                            9921 msgs    25.0 Hz
/dji_osdk_ros/time_sync_pps_source            std_msgs/msg/String                                397 msgs     1.0 Hz
/dji_osdk_ros/velocity                        geometry_msgs/msg/Vector3Stamped                 19851 msgs    50.0 Hz
/dji_osdk_ros/vo_position                     dji_osdk_ros/msg/VOPosition                      19851 msgs    50.0 Hz
/left_camera/image/compressed                 sensor_msgs/msg/CompressedImage                   3971 msgs    10.0 Hz
/livox/imu                                    sensor_msgs/msg/Imu                              82626 msgs   208.1 Hz
/livox/lidar                                  livox_ros_driver/msg/CustomMsg                    3970 msgs    10.0 Hz
/ublox_driver/ephem                           gnss_comm/msg/GnssEphemMsg                         303 msgs     0.8 Hz
/ublox_driver/glo_ephem                       gnss_comm/msg/GnssGloEphemMsg                      105 msgs     0.3 Hz
/ublox_driver/iono_params                     gnss_comm/msg/StampedFloat64Array                  116 msgs     0.3 Hz
/ublox_driver/range_meas                      gnss_comm/msg/GnssMeasMsg                         3970 msgs    10.0 Hz
/ublox_driver/receiver_lla                    sensor_msgs/msg/NavSatFix                         3970 msgs    10.0 Hz
/ublox_driver/receiver_pvt                    gnss_comm/msg/GnssPVTSolnMsg                      3970 msgs    10.0 Hz
/ublox_driver/time_pulse_info                 gnss_comm/msg/GnssTimePulseInfoMsg                 397 msgs     1.0 Hz

--- /dji_osdk_ros/imu 
 sensor_msgs__msg__Imu(header=std_msgs__msg__Header(seq=110035, stamp=builtin_interfaces__msg__Time(sec=1698218948, nanosec=1709577, __msgtype__='builtin_interfaces/msg/Time'), frame_id='body_FLU', __msgtype__='std_msgs/msg/Header'), orientation=geometry_msgs__msg__Quaternion(x=-0.009441062110465541, y=-0.015737590181361595, z=-0.6613645524310489, w=0.7498400652067001, __msgtype__='geometry_msgs/msg/Quaternion'), orientation_covariance=array([0., 0., 0., 0., 0., 0., 0., 0., 0.]), angular_velocity=geometry_msgs__msg__Vector3(x=0.0052743107080459595, y=0.0007001617923378944, z=8.48318450152874e-05, __msgtype__='geometry_msgs/msg/Vector3'), angular_velocity_covariance=array([0., 0., 0., 0., 0., 0., 0., 0., 0.]), linear_acceleration=geometry_msgs__msg__Vector3(x=0.14314025196433067, y=-0.037640

--- /dji_osdk_ros/gps_position 
 sensor_msgs__msg__NavSatFix(header=std_msgs__msg__Header(seq=13758, stamp=builtin_interfaces__msg__Time(sec=1698218948, nanosec=12560785, __msgtype__='builtin_interfaces/msg/Time'), frame_id='/gps', __msgtype__='std_msgs/msg/Header'), status=sensor_msgs__msg__NavSatStatus(status=0, service=0, STATUS_NO_FIX=-1, STATUS_FIX=0, STATUS_SBAS_FIX=1, STATUS_GBAS_FIX=2, SERVICE_GPS=1, SERVICE_GLONASS=2, SERVICE_COMPASS=4, SERVICE_GALILEO=8, __msgtype__='sensor_msgs/msg/NavSatStatus'), latitude=22.41611623603051, longitude=114.04270875886002, altitude=70.37698364257812, position_covariance=array([ 657.,    0.,    0.,    0.,  657.,    0.,    0.,    0., 1156.]), position_covariance_type=0, COVARIANCE_TYPE_UNKNOWN=0, COVARIANCE_TYPE_APPROXIMATED=1, COVARIANCE_TYPE_DIAGONAL_KNOWN=2, COVARIANCE_TYPE_KNOW

--- /livox/imu 
 sensor_msgs__msg__Imu(header=std_msgs__msg__Header(seq=56578, stamp=builtin_interfaces__msg__Time(sec=1698218948, nanosec=305227041, __msgtype__='builtin_interfaces/msg/Time'), frame_id='livox_frame', __msgtype__='std_msgs/msg/Header'), orientation=geometry_msgs__msg__Quaternion(x=0.0, y=0.0, z=0.0, w=0.0, __msgtype__='geometry_msgs/msg/Quaternion'), orientation_covariance=array([0., 0., 0., 0., 0., 0., 0., 0., 0.]), angular_velocity=geometry_msgs__msg__Vector3(x=0.00452003488317132, y=-0.002623864682391286, z=-7.788464426994324e-05, __msgtype__='geometry_msgs/msg/Vector3'), angular_velocity_covariance=array([0., 0., 0., 0., 0., 0., 0., 0., 0.]), linear_acceleration=geometry_msgs__msg__Vector3(x=-0.9942338466644287, y=-0.0016920464113354683, z=-0.00854542851448059, __msgtype__='geometry_ms

--- /ublox_driver/range_meas 
 gnss_comm__msg__GnssMeasMsg(meas=[gnss_comm__msg__GnssObsMsg(time=gnss_comm__msg__GnssTimeMsg(week=2285, tow=286166.4, __msgtype__='gnss_comm/msg/GnssTimeMsg'), sat=10, freqs=array([1.57542e+09, 1.22760e+09]), CN0=array([37., 25.]), LLI=array([0, 3], dtype=uint8), code=array([ 1, 17], dtype=uint8), psr=array([21862292.01999154, 21862307.4219312 ]), psr_std=array([0.32, 2.56]), cp=array([1.14887117e+08, 0.00000000e+00]), cp_std=array([0.012, 0.06 ]), dopp=array([-4156.12304688, -3238.45751953]), dopp_std=array([0.256, 2.048]), status=array([7, 1], dtype=uint8), __msgtype__='gnss_comm/msg/GnssObsMsg'), gnss_comm__msg__GnssObsMsg(time=gnss_comm__msg__GnssTimeMsg(week=2285, tow=286166.4, __msgtype__='gnss_comm/msg/GnssTimeMsg'), sat=29, freqs=array([1.57542e+09, 1.22760e+09]), CN0=array([34., 

--- /left_camera/image/compressed 
 sensor_msgs__msg__CompressedImage(header=std_msgs__msg__Header(seq=185, stamp=builtin_interfaces__msg__Time(sec=1698218948, nanosec=199970007, __msgtype__='builtin_interfaces/msg/Time'), frame_id='', __msgtype__='std_msgs/msg/Header'), format='rgb8; jpeg compressed bgr8', data=array([255, 216, 255, ..., 199, 255, 217], dtype=uint8), __msgtype__='sensor_msgs/msg/CompressedImage')

--- /dji_osdk_ros/rtk_position 
 sensor_msgs__msg__NavSatFix(header=std_msgs__msg__Header(seq=1375, stamp=builtin_interfaces__msg__Time(sec=1698218948, nanosec=91927545, __msgtype__='builtin_interfaces/msg/Time'), frame_id='', __msgtype__='std_msgs/msg/Header'), status=sensor_msgs__msg__NavSatStatus(status=0, service=0, STATUS_NO_FIX=-1, STATUS_FIX=0, STATUS_SBAS_FIX=1, STATUS_GBAS_FIX=2, SERVICE_GPS=1, SERVICE_GLONASS=2, SERVICE_COMPASS=4, SERVICE_GALILEO=8, __msgtype__='sensor_msgs/msg/NavSatStatus'), latitude=22.41611881841275, longitude=114.04270602045595, altitude=99.69918823242188, position_covariance=array([0., 0., 0., 0., 0., 0., 0., 0., 0.]), position_covariance_type=0, COVARIANCE_TYPE_UNKNOWN=0, COVARIANCE_TYPE_APPROXIMATED=1, COVARIANCE_TYPE_DIAGONAL_KNOWN=2, COVARIANCE_TYPE_KNOWN=3, __msgtype__='sensor_msgs/ms

--- /ublox_driver/receiver_pvt 
 gnss_comm__msg__GnssPVTSolnMsg(time=gnss_comm__msg__GnssTimeMsg(week=2285, tow=286166.4, __msgtype__='gnss_comm/msg/GnssTimeMsg'), fix_type=3, valid_fix=True, diff_soln=False, carr_soln=0, num_sv=26, latitude=22.4161634, longitude=114.0427055, altitude=91.071, height_msl=93.276, h_acc=0.679, v_acc=1.149, p_dop=1.17, vel_n=0.026000000000000002, vel_e=-0.041, vel_d=-0.01, vel_acc=0.14300000000000002, __msgtype__='gnss_comm/msg/GnssPVTSolnMsg')

--- /ublox_driver/receiver_lla 
 sensor_msgs__msg__NavSatFix(header=std_msgs__msg__Header(seq=2044, stamp=builtin_interfaces__msg__Time(sec=1698218966, nanosec=400000095, __msgtype__='builtin_interfaces/msg/Time'), frame_id='', __msgtype__='std_msgs/msg/Header'), status=sensor_msgs__msg__NavSatStatus(status=3, service=0, STATUS_NO_FIX=-1, STATUS_FIX=0, STATUS_SBAS_FIX=1, STATUS_GBAS_FIX=2, SERVICE_GPS=1, SERVICE_GLONASS=2, SERVICE_COMPASS=4, SERVICE_GALILEO=8, __msgtype__='sensor_msgs/msg/NavSatStatus'), latitude=22.4161634, longitude=114.0427055, altitude=91.071, position_covariance=array([0., 0., 0., 0., 0., 0., 0., 0., 0.]), position_covariance_type=0, COVARIANCE_TYPE_UNKNOWN=0, COVARIANCE_TYPE_APPROXIMATED=1, COVARIANCE_TYPE_DIAGONAL_KNOWN=2, COVARIANCE_TYPE_KNOWN=3, __msgtype__='sensor_msgs/msg/NavSatFix')

--- /ublox_driver/time_pulse_info 
 gnss_comm__msg__GnssTimePulseInfoMsg(time=gnss_comm__msg__GnssTimeMsg(week=2285, tow=286150.0, __msgtype__='gnss_comm/msg/GnssTimeMsg'), utc_based=True, time_sys=0, __msgtype__='gnss_comm/msg/GnssTimePulseInfoMsg')

--- /ublox_driver/ephem 
 gnss_comm__msg__GnssEphemMsg(sat=62, ttr=gnss_comm__msg__GnssTimeMsg(week=2285, tow=286159.0, __msgtype__='gnss_comm/msg/GnssTimeMsg'), toe=gnss_comm__msg__GnssTimeMsg(week=2285, tow=285000.0, __msgtype__='gnss_comm/msg/GnssTimeMsg'), toc=gnss_comm__msg__GnssTimeMsg(week=2285, tow=285000.0, __msgtype__='gnss_comm/msg/GnssTimeMsg'), toe_tow=285000.0, week=2285, iode=91, iodc=91, health=0, code=513, ura=3.12, A=29600287.67392637, e=0.0003312358167022466, i0=0.9591786540752828, omg=1.1449739870709532, OMG0=1.7061891317058218, M0=1.1439681694488977, delta_n=3.3612114365193526e-09, OMG_dot=-5.934175753667946e-09, i_dot=-6.521700226420505e-10, cuc=-8.238479495048523e-06, cus=6.4838677644729614e-06, crc=205.125, crs=-174.09375, cic=1.1548399925231934e-07, cis=0.0, af0=-6.798771210014819e-05, af1=

--- /ublox_driver/iono_params 
 gnss_comm__msg__StampedFloat64Array(header=std_msgs__msg__Header(seq=12, stamp=builtin_interfaces__msg__Time(sec=1698218954, nanosec=0, __msgtype__='builtin_interfaces/msg/Time'), frame_id='', __msgtype__='std_msgs/msg/Header'), data=array([ 5.12227416e-08,  6.70552254e-08, -1.37090683e-06,  2.20537186e-06,
        1.26976000e+05, -3.11296000e+05,  1.24518400e+06, -2.62144000e+05]), __msgtype__='gnss_comm/msg/StampedFloat64Array')

--- /ublox_driver/glo_ephem 
 gnss_comm__msg__GnssGloEphemMsg(sat=33, ttr=gnss_comm__msg__GnssTimeMsg(week=2285, tow=286188.0, __msgtype__='gnss_comm/msg/GnssTimeMsg'), toe=gnss_comm__msg__GnssTimeMsg(week=2285, tow=285318.0, __msgtype__='gnss_comm/msg/GnssTimeMsg'), freqo=1, iode=41, health=0, age=0, ura=2.5, pos_x=-11686296.875, pos_y=17740127.44140625, pos_z=14118664.55078125, vel_x=-1444.2663192749023, vel_y=1292.2964096069336, vel_z=-2817.4171447753906, acc_x=1.862645149230957e-06, acc_y=-1.862645149230957e-06, acc_z=-1.862645149230957e-06, tau_n=-6.837956607341766e-05, gamma=0.0, delta_tau_n=8.381903171539307e-09, __msgtype__='gnss_comm/msg/GnssGloEphemMsg')
