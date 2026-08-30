const { RoomBooking } = require('../models');
const { Op } = require('sequelize');

export async function findRoom(checkin, checkout, room_type, gender, excludeRooms = []) {
  // Simulating the conflict check on the bookings table
  return await RoomBooking.findOne({
    where: {
      room_type,
      gender,
      room_no: { [Op.notIn]: excludeRooms }
    }
  });
}

export async function createRoomBooking(checkin, checkout, room_type, gender) {
  // Pre-fix TOCTOU code
  const conflict = await findRoom(checkin, checkout, room_type, gender);

  if (conflict) {
    throw new Error('Room already booked');
  }

  // Create booking
  await RoomBooking.create({
    room_no: "101",
    checkin,
    checkout,
    gender
  });

  return true;
}
